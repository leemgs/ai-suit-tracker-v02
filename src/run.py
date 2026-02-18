from __future__ import annotations
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .fetch import fetch_news
from .extract import load_known_cases, build_lawsuits_from_news
from .render import render_markdown
from .github_issue import find_or_create_issue, create_comment, close_other_daily_issues
from .github_issue import list_comments, get_first_comment_body
from .slack import post_to_slack
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
    owner = os.environ["GITHUB_OWNER"]
    repo = os.environ["GITHUB_REPO"]
    gh_token = os.environ["GITHUB_TOKEN"]
    slack_webhook = os.environ["SLACK_WEBHOOK_URL"]

    base_title = os.environ.get("ISSUE_TITLE_BASE", "AI 불법/무단 학습데이터 소송 모니터링")
    lookback_days = int(os.environ.get("LOOKBACK_DAYS", "3"))
    # 필요 시 2로 변경: 환경변수 LOOKBACK_DAYS=2
    
    # KST 기준 날짜 생성
    now_kst = datetime.now(ZoneInfo("Asia/Seoul"))
    run_ts_kst = now_kst.strftime("%Y-%m-%d %H:%M")
    issue_day_kst = now_kst.strftime("%Y-%m-%d")
    issue_title = f"{base_title} ({issue_day_kst})"
    print(f"KST 기준 실행시각: {run_ts_kst}")
    
    issue_label = os.environ.get("ISSUE_LABEL", "ai-lawsuit-monitor")

    # 1) CourtListener 검색
    hits = []
    for q in COURTLISTENER_QUERIES:
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
    # 🔥 FIX: RECAP 문서 건수 계산 방식 수정
    # 기존: len(cl_docs)
    # 문제: HTML fallback 등으로 CLCaseSummary에만 complaint_link가 있고
    #       CLDocument가 생성되지 않는 경우 KPI가 0으로 나옴
    # 해결: CLCaseSummary 기준으로 complaint_link 존재 여부 카운트
    # =====================================================
    recap_doc_count = sum(
        1 for c in cl_cases
        if (getattr(c, "complaint_link", "") or "").strip()
    )

    # 3) 렌더링
    md = render_markdown(
        lawsuits,
        cl_docs,
        cl_cases,
        recap_doc_count,
        lookback_days=lookback_days,
    )    
    md = f"### 실행 시각(KST): {run_ts_kst}\n\n" + md
    
    print("===== REPORT BEGIN =====")
    print(md[:1000]) # 로그 너무 길면 잘리므로 일부만 출력
    print("===== REPORT END =====")

    # 4) GitHub Issue 작업
    issue_no = find_or_create_issue(owner, repo, gh_token, issue_title, issue_label)
    issue_url = f"https://github.com/{owner}/{repo}/issues/{issue_no}"
   

    # =========================================================
    # 🔥 Base Snapshot 비교 로직
    # =========================================================
    comments = list_comments(owner, repo, gh_token, issue_no)
    first_run_today = len(comments) == 0

    if not first_run_today:
        base_body = get_first_comment_body(owner, repo, gh_token, issue_no) or ""

        import re

        # =====================================================
        # 🔒 안정형 테이블 기반 비교 로직
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

            header_cols = [c.strip() for c in header.split("|")[1:-1]]

            parsed_rows = []
            for row in rows:
                cols = [c.strip() for c in row.split("|")[1:-1]]
                if len(cols) == len(header_cols):
                    parsed_rows.append(cols)

            return header_cols, parsed_rows, (header, separator)

        def extract_article_url(cell: str):
            m = re.search(r"\((https?://[^\)]+)\)", cell)
            if m:
                return m.group(1).split("&hl=")[0]
            return None

        # -------------------------
        # Base Snapshot Key Set 생성
        # -------------------------
        base_article_set = set()
        base_docket_set = set()

        news_section_base = extract_section(base_body, "## 📰 외부 기사 기반 소송 정보")
        headers, rows, _ = parse_table(news_section_base)
        if "제목" in headers:
            idx = headers.index("제목")
            for r in rows:
                url = extract_article_url(r[idx])
                if url:
                    base_article_set.add(url)

        recap_section_base = extract_section(base_body, "## ⚖️ RECAP")
        headers, rows, _ = parse_table(recap_section_base)
        if "도켓번호" in headers:
            idx = headers.index("도켓번호")
            for r in rows:
                base_docket_set.add(r[idx])

        # -------------------------
        # 현재 md 처리
        # -------------------------
        current_md = md

        # 외부 기사 처리
        news_section = extract_section(current_md, "## 📰 외부 기사 기반 소송 정보")
        headers, rows, table_meta = parse_table(news_section)

        new_article_count = 0
        total_article_count = len(rows)

        if headers and "제목" in headers:
            idx = headers.index("제목")
            header_line, separator_line = table_meta
            new_lines = [header_line, separator_line]

            for r in rows:
                url = extract_article_url(r[idx])
                if url in base_article_set:
                    # 🔥 개선: 핵심 식별 컬럼(No, 기사일자, 제목)은 유지
                    try:
                        no_idx = headers.index("No.")
                        date_idx = headers.index("기사일자⬇️")
                        title_idx = headers.index("제목")
                    except ValueError:
                        no_idx = date_idx = title_idx = None

                    new_row = []
                    for i, col in enumerate(r):
                        if i in (no_idx, date_idx, title_idx):
                            new_row.append(col)
                        else:
                            new_row.append("skip")

                    new_lines.append("| " + " | ".join(new_row) + " |")
                else:
                    new_lines.append("| " + " | ".join(r) + " |")
                    new_article_count += 1

            new_news_section = "\n".join(new_lines)
            current_md = current_md.replace(news_section, new_news_section)

        # RECAP 처리
        recap_section = extract_section(current_md, "## ⚖️ RECAP")
        headers, rows, table_meta = parse_table(recap_section)

        new_docket_count = 0
        total_docket_count = len(rows)

        if headers and "도켓번호" in headers:
            idx = headers.index("도켓번호")
            header_line, separator_line = table_meta
            new_lines = [header_line, separator_line]

            for r in rows:
                docket = r[idx]
                if docket in base_docket_set:
                    # 🔥 개선: 핵심 식별 컬럼(No, 상태, 케이스명, 도켓번호) 유지
                    try:
                        no_idx = headers.index("No.")
                        status_idx = headers.index("상태")
                        case_idx = headers.index("케이스명")
                        docket_idx = headers.index("도켓번호")
                    except ValueError:
                        no_idx = status_idx = case_idx = docket_idx = None

                    new_row = []
                    for i, col in enumerate(r):
                        if i in (no_idx, status_idx, case_idx, docket_idx):
                            new_row.append(col)
                        else:
                            new_row.append("skip")

                    new_lines.append("| " + " | ".join(new_row) + " |")
                else:
                    new_lines.append("| " + " | ".join(r) + " |")
                    new_docket_count += 1

            new_recap_section = "\n".join(new_lines)
            current_md = current_md.replace(recap_section, new_recap_section)

        # -------------------------
        # Summary 생성
        # -------------------------
        summary_header = (
            "### 자료 중복 제거 결과 요약:\n"
            f"1). 외부 기사 기반 소송 정보: 기존 {len(base_article_set)}건 (base snapshot) "
            f"+ 신규 {new_article_count}건 = 총 {total_article_count}건\n"
            f"2). RECAP: 기존 {len(base_docket_set)}건 (base snapshot) "
            f"+ 신규 {new_docket_count}건 = 총 {total_docket_count}건\n\n"
        )

        md = summary_header + current_md 

    # 이전 날짜 이슈 Close
    closed_nums = close_other_daily_issues(owner, repo, gh_token, issue_label, base_title, issue_title, issue_no, issue_url)
    if closed_nums:
        print(f"이전 날짜 이슈 자동 Close: {closed_nums}")
    
    # KST 기준 타임스탬프
    timestamp = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M KST")

    comment_body = f"\n\n{md}"
    create_comment(owner, repo, gh_token, issue_no, comment_body)
    print(f"Issue #{issue_no} 댓글 업로드 완료")

    # 5) Slack 요약 전송
    # ============================================
    # 🔥 Slack 출력 개선 (최종 포맷)
    # ============================================

    import re

    base_news = new_news = total_news = None
    base_cases = new_cases = total_cases = None

    if "### 자료 중복 제거 결과 요약:" in md:

        m_news = re.search(
            r"외부 기사 기반 소송 정보: 기존 (\d+)건 .*?\+ 신규 (-?\d+)건 = 총 (\d+)건",
            md,
        )

        m_cases = re.search(
            r"RECAP: 기존 (\d+)건 .*?\+ 신규 (-?\d+)건 = 총 (\d+)건",
            md,
        )

        if m_news:
            base_news = int(m_news.group(1))
            new_news = int(m_news.group(2))
            total_news = int(m_news.group(3))

        if m_cases:
            base_cases = int(m_cases.group(1))
            new_cases = int(m_cases.group(2))
            total_cases = int(m_cases.group(3))

    def format_delta(n: int) -> str:
        if n > 0:
            return f"+{n}"
        elif n < 0:
            return f"{n}"
        else:
            return "0"

    slack_lines = []

    slack_lines.append("📊 AI 소송 모니터링")
    slack_lines.append(f"🕒 {timestamp}")
    slack_lines.append("")

    # 🔁 Dedup Summary
    if base_news is not None and base_cases is not None:
        slack_lines.append("🔁 Dedup Summary")
        slack_lines.append(
            f"└ News: {base_news} → {format_delta(new_news)} = {total_news}"
        )
        slack_lines.append(
            f"└ Cases: {base_cases} → {format_delta(new_cases)} = {total_cases}"
        )
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

            if docket_id:
                docket_url = f"https://www.courtlistener.com/docket/{docket_id}/"
                slack_lines.append(
                    f"• {date} | <{docket_url}|{name}>"
                )
            else:
                slack_lines.append(f"• {date} | {name}")

    post_to_slack(slack_webhook, "\n".join(slack_lines))
    print("Slack 전송 완료")

if __name__ == "__main__":
    main()
