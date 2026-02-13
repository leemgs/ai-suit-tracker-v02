from __future__ import annotations
from typing import List
from collections import Counter
import re
from .extract import Lawsuit
from .courtlistener import CLDocument, CLCaseSummary


def _esc(s: str) -> str:
    s = str(s or "").strip()
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = s.replace("```", "&#96;&#96;&#96;")
    s = s.replace("~~~", "&#126;&#126;&#126;")
    s = s.replace("|", "\\|")
    s = s.replace("\n", "<br>")
    return s


def _md_sep(col_count: int) -> str:
    return "|" + "---|" * col_count


def _mdlink(label: str, url: str) -> str:
    label = _esc(label)
    url = (url or "").strip()
    if not url:
        return label

    # 🔥 이미 Markdown 링크 형식이면 그대로 반환 (이중 방지)
    if url.startswith("[") and "](" in url:
        return url
        
    return f"[{label}]({url})"


def _short(val: str, limit: int = 140) -> str:
    val = val or ""
    if len(val) <= limit:
        return _esc(val)
    return f"<details><summary>내용 펼치기</summary>{_esc(val)}</details>"


# =====================================================
# slug 변환
# =====================================================
def _slugify_case_name(name: str) -> str:
    name = (name or "").lower()
    name = name.replace("v.", "v")
    name = re.sub(r"[^a-z0-9\s-]", "", name)
    name = re.sub(r"\s+", "-", name)
    name = re.sub(r"-+", "-", name)
    return name.strip("-")


# =====================================================
# 뉴스 위험도
# =====================================================
def calculate_news_risk_score(title: str, reason: str) -> int:
    score = 0
    text = f"{title or ''} {reason or ''}".lower()

    if any(k in text for k in ["scrape", "crawl", "unauthorised", "unauthorized"]):
        score += 30
    if any(k in text for k in ["train", "training", "model", "llm"]):
        score += 30
    if any(k in text for k in ["copyright", "dmca", "infringement"]):
        score += 20
    if "class action" in text:
        score += 10
    if any(k in text for k in ["billion", "$"]):
        score += 10

    return min(score, 100)


def format_risk(score: int) -> str:
    if score >= 80:
        return f"🔥 {score}"
    if score >= 60:
        return f"⚠️ {score}"
    if score >= 40:
        return f"🟡 {score}"
    return f"🟢 {score}"


# =====================================================
# RECAP 위험도
# =====================================================
def calculate_case_risk_score(case: CLCaseSummary) -> int:
    score = 0
    text = f"{case.extracted_ai_snippet or ''} {case.extracted_causes or ''}".lower()

    if any(k in text for k in ["scrape", "crawl", "ingest", "harvest"]):
        score += 30
    if any(k in text for k in ["train", "training", "model", "llm", "neural"]):
        score += 30
    if any(k in text for k in ["commercial", "profit"]):
        score += 15
    if case.nature_of_suit and "820" in case.nature_of_suit:
        score += 15
    if "class action" in text:
        score += 10

    return min(score, 100)


# =====================================================
# 메인 렌더
# =====================================================
def render_markdown(
    lawsuits: List[Lawsuit],
    cl_cases: List[CLCaseSummary],
    lookback_days: int = 3,
) -> str:

    lines: List[str] = []

    # KPI
    lines.append(f"## 📊 최근 {lookback_days}일 요약\n")
    lines.append("| 구분 | 건수 |")
    lines.append("|---|---|")
    lines.append(f"| 📰 뉴스 수집 | **{len(lawsuits)}** |")
    lines.append(f"| ⚖️ RECAP 사건 | **{len(cl_cases)}** |")
    lines.append(f"\n")

    # Nature 통계
    if cl_cases:
        counter = Counter([c.nature_of_suit or "미확인" for c in cl_cases])
        lines.append("## 📊 Nature of Suit 통계\n")
        lines.append("| Nature of Suit | 건수 |")
        lines.append("|---|---|")
        for k, v in counter.most_common(10):
            lines.append(f"| {_esc(k)} | **{v}** |")
        lines.append("")

    # AI Top3
    if cl_cases:
        lines.append("## 🧠 최근 소송 (Top 3)\n")
        top_cases = sorted(cl_cases, key=lambda x: x.date_filed, reverse=True)[:3]
        for c in top_cases:
            lines.append(f"> **{_esc(c.case_name)}**")
            lines.append(f"> {_short(c.extracted_ai_snippet, 120)}\n")

    # 뉴스 테이블
    if lawsuits:
        lines.append("## 📰 뉴스/RSS 기반 소송 요약")
        lines.append("| 일자 | 제목 | 소송번호 | 사유 | 위험도 예측 점수 |")
        lines.append(_md_sep(5))

        for s in lawsuits:
            article_url = s.article_urls[0] if getattr(s, "article_urls", None) else ""
            title_cell = _mdlink(s.article_title or s.case_title, article_url)

            risk_score = calculate_news_risk_score(
                s.article_title or s.case_title, s.reason
            )

            lines.append(
                f"| {_esc(s.update_or_filed_date)} | "
                f"{title_cell} | "
                f"{_esc(s.case_number)} | "
                f"{_short(s.reason)} | "
                f"{format_risk(risk_score)} |"
            )
        lines.append("")

    # RECAP 케이스
    if cl_cases:

        copyright_cases = []
        other_cases = []

        for c in cl_cases:
            if "820" in (c.nature_of_suit or ""):
                copyright_cases.append(c)
            else:
                other_cases.append(c)

        def render_case_table(cases: List[CLCaseSummary]):
            lines.append(
                "| 상태 | 케이스명 | 도켓번호 | Nature | 위험도 | "
                "소송이유 | AI학습관련 핵심주장 | 법적 근거 | 담당판사 | 법원 | "
                "Complaint 문서 번호 | Complaint PDF 링크 | 최근 도켓 업데이트 |"
            )
            lines.append(_md_sep(13))

            for c in sorted(cases, key=lambda x: x.date_filed, reverse=True):
                slug = _slugify_case_name(c.case_name)
                docket_url = f"https://www.courtlistener.com/docket/{c.docket_id}/{slug}/"
                score = calculate_case_risk_score(c)

                if c.court_short_name and c.court_api_url:
                    court_display = _mdlink(c.court_short_name, c.court_api_url)
                else:
                    court_display = _esc(c.court)

                lines.append(
                    f"| {_esc(c.status)} | "
                    f"{_mdlink(c.case_name, docket_url)} | "
                    f"{_mdlink(c.docket_number, docket_url)} | "
                    f"{_esc(c.nature_of_suit)} | "
                    f"{format_risk(score)} | "
                    f"{_short(c.extracted_causes, 120)} | "
                    f"{_short(c.extracted_ai_snippet, 120)} | "
                    f"{_esc(c.cause)} | "
                    f"{_esc(c.judge)} | "
                    f"{court_display} | "
                    f"{_esc(c.complaint_doc_no)} | "
                    f"{_mdlink('PDF', c.complaint_link) if c.complaint_link else ''} | "
                    f"{_esc(c.recent_updates)} |"
                )

        lines.append("## 🔥 RECAP 1/2: 820 Copyright\n")
        if copyright_cases:
            render_case_table(copyright_cases)
        else:
            lines.append("820 사건 없음\n")

        lines.append("## 📁 RECAP 2/2: Others\n")
        if other_cases:
            render_case_table(other_cases)
        else:
            lines.append("Others 사건 없음\n")


    # 기사 주소
    if lawsuits:
        lines.append("<details>")
        lines.append("<summary><strong><span style=\"font-size:2.5em; font-weight:bold;\">📰 기사 주소</span></strong></summary>\n")
        for s in lawsuits:
            lines.append(f"### {_esc(s.article_title or s.case_title)}")
            for u in s.article_urls:
                lines.append(f"- {u}")
        lines.append("</details>\n")

    # 위험도 척도
    lines.append("<details>")
    lines.append("<summary><strong><span style=\"font-size:2.5em; font-weight:bold;\">📘 AI 학습 위험도 점수(0~100) 평가 척도</span></strong></summary>\n")
    lines.append("- AI 모델 학습과의 직접성 + 법적 리스크 강도를 수치화한 지표입니다.")
    lines.append("- 0에 가까울수록 → 간접/주변 이슈")
    lines.append("- 100에 가까울수록 → AI 학습 핵심 리스크 사건\n")
    lines.append("")
    
    lines.append("### 📊 등급 기준")
    lines.append("- 00~039 🟢 : 간접 연관")
    lines.append("- 40~059 🟡 : 학습 쟁점 존재")
    lines.append("- 60~079 ⚠️ : 모델 학습 직접 언급")
    lines.append("- 80~100 🔥 : 무단 수집 + 학습 + 상업적 사용 고위험")
    lines.append("")

    lines.append("### 🧮 점수 산정 기준")
    lines.append("| 항목 | 조건 | 점수 |")
    lines.append("|---|---|---|")
    lines.append("| 무단 데이터 수집 명시 | scrape / crawl / ingest | +30 |")
    lines.append("| 모델 학습 직접 언급 | train / training / model | +30 |")
    lines.append("| 상업적 사용 | commercial / profit | +15 |")
    lines.append("| 저작권 소송 (820) | Nature = 820 | +15 |")
    lines.append("| 집단소송 | class action | +10 |")
    lines.append("")

    lines.append("</details>\n")

    return "\n".join(lines) or ""
