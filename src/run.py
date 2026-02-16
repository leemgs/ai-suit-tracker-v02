from __future__ import annotations
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .fetch import fetch_news
from .extract import load_known_cases, build_lawsuits_from_news
from .render import render_markdown
from .github_issue import find_or_create_issue, create_comment, close_other_daily_issues
from .github_issue import list_comments
from .slack import post_to_slack
from urllib.parse import urlparse, urlunparse
import re
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
    recap_doc_count = len(cl_docs)

    # 3) 렌더링
    md = render_markdown(lawsuits, cl_docs, cl_cases, lookback_days=lookback_days)
    md = f"### 실행 시각(KST): {run_ts_kst}\n\n" + md
   

    # ==========================================
    # 🔹 노이즈 제거 + URL Canonicalization
    # ==========================================

    def normalize_url(url: str) -> str:
        try:
            parsed = urlparse(url)
            return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))
        except Exception:
            return url

    cleaned_lines = []
    seen_urls = set()

    for line in md.splitlines():
        stripped = line.strip()

        # Google RSS 제거
        if "news.google.com/rss" in stripped:
            continue
        # bullet URL 처리
        if stripped.startswith("- http"):
            raw_url = stripped[2:].strip()
            normalized = normalize_url(raw_url)

            # CourtListener 외 URL 제거 (RECAP 보호)
            if "courtlistener.com" not in normalized:
                continue

            if normalized in seen_urls:
                continue

            seen_urls.add(normalized)
            cleaned_lines.append(f"- {normalized}")
        else:
            cleaned_lines.append(line)

    md = "\n".join(cleaned_lines)   
    
    print("===== REPORT BEGIN =====")
    print(md[:1000]) # 로그 너무 길면 잘리므로 일부만 출력
    print("===== REPORT END =====")

    # 4) GitHub Issue 작업
    issue_no = find_or_create_issue(owner, repo, gh_token, issue_title, issue_label)
    issue_url = f"https://github.com/{owner}/{repo}/issues/{issue_no}"
    
    # 이전 날짜 이슈 Close
    closed_nums = close_other_daily_issues(owner, repo, gh_token, issue_label, base_title, issue_title, issue_no, issue_url)
    if closed_nums:
        print(f"이전 날짜 이슈 자동 Close: {closed_nums}")
    
    # KST 기준 타임스탬프
    timestamp = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M KST")

    comments = list_comments(owner, repo, gh_token, issue_no)

    # 최초 생성 (댓글이 하나도 없는 경우)
    if not comments:
        create_comment(owner, repo, gh_token, issue_no, md)
        print(f"Issue #{issue_no} 최초 전체 리포트 등록 완료")
    else:
        last_comment_body = comments[-1].get("body", "")

        def split_sections(text):
            sections = {}
            current = None
            buffer = []
            for line in text.splitlines():
                if line.startswith("## "):
                    if current:
                        sections[current] = "\n".join(buffer)
                    current = line.strip()
                    buffer = []
                else:
                    buffer.append(line)
            if current:
                sections[current] = "\n".join(buffer)
            return sections

        def extract_bullets(section_text):
            bullets = set()
            for line in section_text.splitlines():
                stripped = line.strip()
                # 🔹 실제 데이터 항목만 허용 (URL 포함 항목만)
                if stripped.startswith("- ") and "http" in stripped:
                    raw_url = stripped.split("http", 1)[1]
                    raw_url = "http" + raw_url
                    parsed = urlparse(raw_url)
                    normalized = urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))
                    bullets.add(f"- {normalized}")
            return bullets

        sections_now = split_sections(md)
        sections_prev = split_sections(last_comment_body)

        output_lines = []
        total_new = 0

        output_lines.append(f"### 실행 시각(KST): {timestamp}")
        output_lines.append("")

        for title, content_now in sections_now.items():
            output_lines.append(title)

            bullets_now = extract_bullets(content_now)
            bullets_prev = extract_bullets(sections_prev.get(title, ""))

            new_items = sorted(bullets_now - bullets_prev)

            if new_items:
                total_new += len(new_items)
                output_lines.extend(new_items)
            else:
                output_lines.append("- 새롭게 추가된 정보 없음.")

            output_lines.append("")

        if total_new == 0:
            create_comment(owner, repo, gh_token, issue_no, "새롭게 추가된 정보가 없슴니다.")
            print("신규 데이터 없음")
        else:
            create_comment(owner, repo, gh_token, issue_no, "\n".join(output_lines))
            print("신규 항목만 업데이트 완료")

    # 5) Slack 요약 전송
    summary_lines = [
        f"*AI 소송 모니터링 업데이트* ({timestamp})",
        f"- 언론보도 기반 수집 건수: {len(lawsuits)}건",
        f"- 법원 사건(RECAP 도켓) 확인 건수: {docket_case_count}건",
        f"- 법원 문서(RECAP Complaint 등) 확보 건수: {recap_doc_count}건",
        f"- GitHub Issue (For more details): <{issue_url}|#{issue_no}>",
    ]
    
    if cl_docs:
        # date_filed 기준으로 정렬
        top = sorted(cl_docs, key=lambda x: getattr(x, 'date_filed', ''), reverse=True)[:3]
        summary_lines.append("- 최신 RECAP 문서:")
        for d in top:
            date = getattr(d, 'date_filed', 'N/A')
            name = getattr(d, 'case_name', 'Unknown Case')
            summary_lines.append(f"  • {date} | {name}")
    
    post_to_slack(slack_webhook, "\n".join(summary_lines))
    print("Slack 전송 완료")

if __name__ == "__main__":
    main()

