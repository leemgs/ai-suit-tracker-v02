from __future__ import annotations
import os
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .fetch import fetch_news
from .extract import load_known_cases, build_lawsuits_from_news
from .render import render_markdown
from .github_issue import find_or_create_issue, create_comment, close_other_daily_issues
from .github_issue import list_comments
from .slack import post_to_slack
from .utils import debug_log
from .courtlistener import (
    search_recent_documents,
    build_complaint_documents_from_hits,
    build_case_summaries_from_hits,
    build_case_summaries_from_docket_numbers,
    build_case_summaries_from_case_titles,
    build_documents_from_docket_ids,
)
from .queries import COURTLISTENER_QUERIES

def main() -> None:
    # 0) 환경 변수 로드
    owner = os.environ.get("GITHUB_OWNER")
    repo = os.environ.get("GITHUB_REPO")
    gh_token = os.environ.get("GITHUB_TOKEN")
    slack_webhook = os.environ.get("SLACK_WEBHOOK_URL")

    if not all([owner, repo, gh_token, slack_webhook]):
        missing = [k for k, v in {"GITHUB_OWNER": owner, "GITHUB_REPO": repo, "GITHUB_TOKEN": gh_token, "SLACK_WEBHOOK_URL": slack_webhook}.items() if not v]
        raise ValueError(f"필수 환경 변수가 누락되었습니다: {', '.join(missing)}")

    base_title = os.environ.get("ISSUE_TITLE_BASE", "AI 불법/무단 학습데이터 소송 모니터링")
    lookback_days = int(os.environ.get("LOOKBACK_DAYS", "3"))
    # 필요 시 2로 변경: 환경변수 LOOKBACK_DAYS=2
    
    # KST 기준 날짜 생성
    now_kst = datetime.now(ZoneInfo("Asia/Seoul"))
    run_ts_kst = now_kst.strftime("%Y-%m-%d %H:%M")
    issue_day_kst = now_kst.strftime("%Y-%m-%d")
    issue_title = f"{base_title} ({issue_day_kst})"
    debug_log(f"KST 기준 실행시각: {run_ts_kst}")
    
    issue_label = os.environ.get("ISSUE_LABEL", "ai-lawsuit-monitor")

    # 1) CourtListener 검색
    hits = []
    for q in COURTLISTENER_QUERIES:
        debug_log(f"Running CourtListener query: {q}")
        hits.extend(search_recent_documents(q, days=lookback_days, max_results=20))
    
    # 중복 제거
    dedup = {}
    for h in hits:
        key = (h.get("absolute_url") or h.get("url") or "") + "|" + (h.get("caseName") or h.get("title") or "")
        dedup[key] = h
    hits = list(dedup.values())

    cl_docs = build_complaint_documents_from_hits(hits, days=lookback_days)
    # RECAP 도켓(사건) 요약: "법원 사건(도켓) 확인 건수"로 사용
    cl_cases = build_case_summaries_from_hits(hits)

    # 2) 뉴스 수집
    news = fetch_news()
    known = load_known_cases()
    lawsuits = build_lawsuits_from_news(news, known, lookback_days=lookback_days)

    # 2-1) 뉴스 테이블의 소송번호(도켓번호)로 RECAP 도켓/문서 확장
    docket_numbers = [s.case_number for s in lawsuits if (s.case_number or "").strip() and s.case_number != "미확인"]
    extra_cases = build_case_summaries_from_docket_numbers(docket_numbers)

    # 2-2) 소송번호가 없더라도, '소송제목'(추정 케이스명)으로 도켓 확장
    case_titles = [s.case_title for s in lawsuits if (s.case_title or "").strip() and s.case_title != "미확인"]
    extra_cases_by_title = build_case_summaries_from_case_titles(case_titles)

    merged_cases = {c.docket_id: c for c in (cl_cases + extra_cases + extra_cases_by_title)}
    cl_cases = list(merged_cases.values())

    # 문서도 docket id 기반으로 추가 시도(Complaint 우선, 없으면 fallback)
    docket_ids = list(merged_cases.keys())
    extra_docs = build_documents_from_docket_ids(docket_ids, days=lookback_days)
    merged_docs = {}
    for d in (cl_docs + extra_docs):
        key = (d.docket_id, d.doc_number, d.date_filed, d.document_url)
        merged_docs[key] = d
    cl_docs = list(merged_docs.values())

    docket_case_count = len(cl_cases)
    
    # =====================================================
    # FIX: RECAP 문서 건수 계산 방식 수정
    # 기존: len(cl_docs)
    # 문제: HTML fallback 등으로 CLCaseSummary에만 complaint_link가 있고
    #       CLDocument가 생성되지 않는 경우 KPI가 0으로 나옴
    # 해결: CLCaseSummary 기준으로 complaint_link 존재 여부 카운트
    # =====================================================
    recap_doc_count = len(cl_docs)

    # 3) 렌더링
    md = render_markdown(
        lawsuits,
        cl_docs,
        cl_cases,
        recap_doc_count,
        lookback_days=lookback_days,
    )    
    md = f"### 실행 시각(KST): {run_ts_kst}\n\n" + md
    
    debug_log(f"📊 수집 및 분석 완료 (최근 {lookback_days}일)")
    debug_log(f"  ├ News: {len(lawsuits)}건")
    debug_log(f"  └ Cases (CourtListener+RECAP): {docket_case_count}건 (문서 {recap_doc_count}건)")

    debug_log("===== REPORT PREVIEW (First 1000 chars) =====")
    debug_log(md[:1000])
    debug_log(f"Report full length: {len(md)}")

    # 4) GitHub Issue 작업
    issue_no = find_or_create_issue(owner, repo, gh_token, issue_title, issue_label)
    issue_url = f"https://github.com/{owner}/{repo}/issues/{issue_no}"
   

    # =========================================================
    # Baseline 비교 로직
    # =========================================================
    comments = list_comments(owner, repo, gh_token, issue_no)
    first_run_today = len(comments) == 0

    if not first_run_today:
        # =====================================================
        # 🔒 안정형 테이블 기반 비교 로직 (모든 이전 댓글 대상)
        # =====================================================

        def extract_section(md_text: str, section_title: str) -> str:
            lines = md_text.split("\n")
            start = None
            end = None
            for i, line in enumerate(lines):
                if line.strip().startswith(section_title):
                    start = i + 1
                    continue
                if start and line.startswith("## "):
                    end = i
                    break
            if start is None:
                return ""
            if end is None:
                end = len(lines)
            return "\n".join(lines[start:end])

        def parse_table(section_md: str):
            lines = [l for l in section_md.split("\n") if l.strip().startswith("|")]
            if len(lines) < 3:
                return [], [], ()

            header = lines[0]
            separator = lines[1]
            rows = lines[2:]

            def split_row(row_text: str):
                # 정규식 (?<!\\)\| 를 사용하여 역슬래시로 이스케이프되지 않은 파이프만 분할
                return [c.strip() for c in re.split(r'(?<!\\)\|', row_text.strip())[1:-1]]

            header_cols = split_row(header)

            parsed_rows = []
            for row in rows:
                cols = split_row(row)
                if len(cols) == len(header_cols):
                    parsed_rows.append(cols)
                else:
                    debug_log(f"Table row column mismatch: expected {len(header_cols)}, got {len(cols)}. Row: {row[:100]}...")

            return header_cols, parsed_rows, (header, separator)

        def extract_article_url(cell: str):
            m = re.search(r"\((https?://[^\)]+)\)", cell)
            if m:
                return m.group(1).split("&hl=")[0]
            return None

        # -------------------------
        # Base Snapshot Key Set 생성 (모든 이전 댓글 대상)
        # -------------------------
        base_article_set = set()
        base_docket_set = set()

        for comment in comments:
            body = comment.get("body") or ""
            
            # News 처리
            news_section_base = extract_section(body, "## 📰 News")
            h_news, r_news, _ = parse_table(news_section_base)
            if "제목" in h_news:
                idx = h_news.index("제목")
                for r in r_news:
                    url = extract_article_url(r[idx])
                    if url:
                        base_article_set.add(url)
            
            # Cases 처리
            recap_section_base = extract_section(body, "## ⚖️ Cases")
            h_cases, r_cases, _ = parse_table(recap_section_base)
            if "도켓번호" in h_cases:
                idx = h_cases.index("도켓번호")
                for r in r_cases:
                    base_docket_set.add(r[idx])

        # -------------------------
        # 현재 md 처리
        # -------------------------
        current_md = md

        # 외부 기사 처리
        news_section = extract_section(current_md, "## 📰 News")
        headers, rows, table_meta = parse_table(news_section)

        new_article_count = 0
        total_article_count = len(rows)

        if headers and "제목" in headers:
            title_idx = headers.index("제목")
            no_idx = headers.index("No.") if "No." in headers else None
            date_idx = headers.index("기사일자⬇️") if "기사일자⬇️" in headers else None

            header_line, separator_line = table_meta
            
            non_skip_rows = []
            skip_rows = []

            for r in rows:
                url = extract_article_url(r[title_idx])
                if url in base_article_set:
                    # 개선: 핵심 식별 컬럼(No, 기사일자, 제목)은 유지
                    new_row = []
                    for i, col in enumerate(r):
                        if i in (no_idx, date_idx, title_idx):
                            new_row.append(col)
                        else:
                            new_row.append("skip")
                    skip_rows.append(new_row)
                else:
                    non_skip_rows.append(r)
                    new_article_count += 1
            
            # 합치기: 신규 먼저, 기존(skip) 나중
            final_rows = non_skip_rows + skip_rows
            new_lines = [header_line, separator_line]
            
            for row_idx, r in enumerate(final_rows, start=1):
                if no_idx is not None:
                    r[no_idx] = str(row_idx)
                new_lines.append("| " + " | ".join(r) + " |")

            new_news_section = "\n".join(new_lines)
            current_md = current_md.replace(news_section, new_news_section)

        # Cases 처리
        recap_section = extract_section(current_md, "## ⚖️ Cases")
        headers, rows, table_meta = parse_table(recap_section)

        new_docket_count = 0
        total_docket_count = len(rows)

        if headers and "도켓번호" in headers:
            docket_idx = headers.index("도켓번호")
            no_idx = headers.index("No.") if "No." in headers else None
            status_idx = headers.index("상태") if "상태" in headers else None
            case_idx = headers.index("케이스명") if "케이스명" in headers else None

            header_line, separator_line = table_meta
            
            non_skip_rows = []
            skip_rows = []

            for r in rows:
                docket = r[docket_idx]
                if docket in base_docket_set:
                    # 개선: 핵심 식별 컬럼(No, 상태, 케이스명, 도켓번호) 유지
                    new_row = []
                    for i, col in enumerate(r):
                        if i in (no_idx, status_idx, case_idx, docket_idx):
                            new_row.append(col)
                        else:
                            new_row.append("skip")
                    skip_rows.append(new_row)
                else:
                    non_skip_rows.append(r)
                    new_docket_count += 1

            # 합치기: 신규 먼저, 기존(skip) 나중
            final_rows = non_skip_rows + skip_rows
            new_lines = [header_line, separator_line]
            
            for row_idx, r in enumerate(final_rows, start=1):
                if no_idx is not None:
                    r[no_idx] = str(row_idx)
                new_lines.append("| " + " | ".join(r) + " |")

            new_recap_section = "\n".join(new_lines)
            current_md = current_md.replace(recap_section, new_recap_section)

        # -------------------------
        # Summary 생성
        # -------------------------
        base_news = len(base_article_set)
        base_cases = len(base_docket_set)

        dup_news = total_article_count - new_article_count
        dup_cases = total_docket_count - new_docket_count

        summary_header = (
            "### 중복 제거 요약:\n"
            "🔁 Dedup Summary\n"
            f"└ News {base_news} (Baseline): "
            f"{dup_news} (Dup), "
            f"{new_article_count} (New)\n"
            f"└ Cases {base_cases} (Baseline): "
            f"{dup_cases} (Dup), "
            f"{new_docket_count} (New)\n\n"
        )

        md = summary_header + current_md

    # 이전 날짜 이슈 Close
    closed_nums = close_other_daily_issues(owner, repo, gh_token, issue_label, base_title, issue_title, issue_no, issue_url)
    if closed_nums:
        debug_log(f"이전 날짜 이슈 자동 Close: {closed_nums}")
    
    # KST 기준 타임스탬프
    timestamp = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M KST")

    comment_body = f"\n\n{md}"
    create_comment(owner, repo, gh_token, issue_no, comment_body)
    debug_log(f"Issue #{issue_no} 댓글 업로드 완료")

    # 5) Slack 요약 전송
    # ============================================
    # Slack 출력 개선 (최종 포맷)
    # ============================================

    slack_dedup_news = None
    slack_dedup_cases = None

    if "### 중복 제거 요약:" in md:
        m_news = re.search(
            r"└ News (.+)",
            md,
        )

        m_cases = re.search(
            r"└ Cases (.+)",
            md,
        )

        if m_news:
            slack_dedup_news = m_news.group(1).strip()

        if m_cases:
            slack_dedup_cases = m_cases.group(1).strip()



    slack_lines = []

    slack_lines.append("📊 AI 소송 모니터링")
    slack_lines.append(f"🕒 {timestamp}")
    slack_lines.append("")

    # 🔁 Dedup Summary
    if slack_dedup_news and slack_dedup_cases:
        slack_lines.append("🔁 Dedup Summary")
        slack_lines.append(f"└ News {slack_dedup_news}")
        slack_lines.append(f"└ Cases {slack_dedup_cases}")
        slack_lines.append("")

    # 📈 Collection Status
    slack_lines.append("📈 Collection Status")
    slack_lines.append(f"└ News: {len(lawsuits)}")
    slack_lines.append(
        f"└ Cases: {docket_case_count} (Docs: {recap_doc_count})"
    )
    slack_lines.append("")

    # 🔗 GitHub
    slack_lines.append(f"🔗 GitHub: <{issue_url}|#{issue_no}>")

    # 🆕 최신 RECAP 문서
    if cl_docs:
        top = sorted(
            cl_docs,
            key=lambda x: getattr(x, "date_filed", ""),
            reverse=True,
        )[:3]

        slack_lines.append("")
        slack_lines.append("🆕 최신 RECAP 문서")

        for d in top:
            date = getattr(d, "date_filed", "N/A")
            name = getattr(d, "case_name", "Unknown Case")
            docket_id = getattr(d, "docket_id", None) 
            absolute_url = getattr(d, "absolute_url", None)

            if absolute_url:
                # 가장 정확한 URL (slug 포함)
                docket_url = absolute_url
                if not docket_url.endswith("/"):
                    docket_url += "/"

                slack_lines.append(
                    f"• {date} | <{docket_url}|{name}>"
                )
            elif docket_id:
                # slug 생성 (GitHub 이슈와 동일 구조 맞추기)
                # case_name → slug 변환
                slug = re.sub(r"[^a-zA-Z0-9]+", "-", name).strip("-").lower()

                docket_url = (
                    f"https://www.courtlistener.com/docket/"
                    f"{docket_id}/{slug}/"
                )

                slack_lines.append(
                    f"• {date} | <{docket_url}|{name}>"
                )
            else:
                slack_lines.append(f"• {date} | {name}")
    try:
        post_to_slack(slack_webhook, "\n".join(slack_lines))
        debug_log(f"Slack 전송 완료")
    except Exception as e:
        debug_log(f"Slack 전송 실패: {e}")
        
if __name__ == "__main__":
    main()
