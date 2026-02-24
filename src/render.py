from __future__ import annotations
from typing import List
from collections import Counter
import re
import copy
from .extract import Lawsuit
from .courtlistener import CLDocument, CLCaseSummary
from .utils import debug_log, slugify_case_name

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

    # 이미 Markdown 링크 형식이면 그대로 반환 (이중 방지)
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
    return slugify_case_name(name)


# =====================================================
# 뉴스 위험도
# =====================================================
def calculate_news_risk_score(title: str, reason: str) -> int:
    score = 0
    text = f"{title or ''} {reason or ''}".lower()

    # 1. 무단 데이터 수집 명시 (+30)
    if any(k in text for k in ["scrape", "crawl", "ingest", "harvest", "mining", "extraction", "bulk", "collection", "robots.txt", "common crawl", "laion", "the pile", "bookcorpus", "unauthorized"]):
        score += 30
    
    # 2. 모델 학습 직접 언급 (+30)
    if any(k in text for k in ["train", "training", "model", "llm", "generative ai", "genai", "gpt", "transformer", "weight", "fine-tune", "diffusion", "inference"]):
        score += 30
    
    # 3. 상업적 사용 (+15)
    if any(k in text for k in ["commercial", "profit", "monetiz", "revenue", "subscription", "enterprise", "paid", "for-profit"]):
        score += 15
    
    # 4. 저작권 관련 (뉴스에서는 Nature of Suit 820 대용으로 키워드 체크) (+15)
    if any(k in text for k in ["copyright", "infringement", "dmca", "fair use", "derivative", "exclusive", "820"]):
        score += 15
        
    # 5. 집단소송 (+10)
    if any(k in text for k in ["class action", "putative class", "representative"]):
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

    # 1. 무단 데이터 수집 명시 (+30)
    if any(k in text for k in ["scrape", "crawl", "ingest", "harvest", "mining", "extraction", "bulk", "collection", "robots.txt", "common crawl", "laion", "the pile", "bookcorpus", "unauthorized"]):
        score += 30
    
    # 2. 모델 학습 직접 언급 (+30)
    if any(k in text for k in ["train", "training", "model", "llm", "generative ai", "genai", "gpt", "transformer", "weight", "fine-tune", "diffusion", "inference"]):
        score += 30
    
    # 3. 상업적 사용 (+15)
    if any(k in text for k in ["commercial", "profit", "monetiz", "revenue", "subscription", "enterprise", "paid", "for-profit"]):
        score += 15
    
    # 4. 저작권 소송 (Nature = 820) (+15)
    # RECAP의 경우 Nature of Suit 코드를 우선하며, 텍스트에서도 저작권 침해 쟁점을 확인합니다.
    if (case.nature_of_suit and "820" in case.nature_of_suit) or any(k in text for k in ["copyright", "infringement", "dmca", "fair use", "derivative", "exclusive"]):
        score += 15
        
    # 5. 집단소송 (+10)
    if any(k in text for k in ["class action", "putative class", "representative"]):
        score += 10

    return min(score, 100)


# =====================================================
# 메인 렌더
# =====================================================
def render_markdown(
    lawsuits: List[Lawsuit],
    cl_docs: List[CLDocument],
    cl_cases: List[CLCaseSummary],
    recap_doc_count: int,
    lookback_days: int = 3,
) -> str:

    lines: List[str] = []

    # KPI (간결 텍스트 요약)
    lines.append(f"## 📊 최근 {lookback_days}일 요약")
    lines.append(f"└ 📰 News: {len(lawsuits)}")
    lines.append(f"└ ⚖ Cases: {len(cl_cases)} (Docs: {recap_doc_count})\n")

    # Nature 통계
    if cl_cases:
        counter = Counter([c.nature_of_suit or "미확인" for c in cl_cases])
        lines.append("## 📊 Nature of Suit 통계\n")
        lines.append("| Nature of Suit | 건수 |")
        lines.append("|---|---|")
        for k, v in counter.most_common(10):
            lines.append(f"| {_esc(k)} | **{v}** |")
        # 총 개수 추가
        total_count = sum(counter.values())
        lines.append(f"| **총개수** | **{total_count}** |")            
        lines.append("")

    # AI 소송 Top3 (업데이트 날짜 기준)
    if cl_cases:
        debug_log("'최근 소송 Top 3 (업데이트 날짜 기준)' is printed.")        
        lines.append("## 🧠 최근 소송 Top 3 (업데이트 날짜 기준)\n")
        
        top_cases = sorted(
            cl_cases,
            key=lambda x: x.recent_updates if x.recent_updates != "미확인" else "",
            reverse=True
        )[:3]

        for idx, c in enumerate(top_cases, start=1):
            update_date = c.recent_updates if c.recent_updates != "미확인" else ""
            lines.append(f"**({idx}) {_esc(update_date or '미확인')}, {_esc(c.case_name)}**")
            
            # Nature
            nature_val = _esc(c.nature_of_suit)
            if nature_val == "820 Copyright":
                nature_val = "⚠️**820 Copyright**"
            
            lines.append(f"   - **Nature**: {nature_val}")
            lines.append(f"   - **소송이유**: {_esc(c.extracted_causes or c.cause or '미확인')}")
            
            # AI학습관련 핵심주장 (Snippet)
            if c.extracted_ai_snippet:
                lines.append(f"   - **AI학습관련 핵심주장**: {_short(c.extracted_ai_snippet, 200)}")
            else:
                lines.append(f"   - **AI학습관련 핵심주장**: 미확인")
            lines.append("")

    # 뉴스 테이블
    lines.append("## 📰 News")
    if lawsuits:
        debug_log("'News' is printed.")            
        lines.append("| No. | 기사일자⬇️ | 제목 | 소송번호 | 소송사유 | 위험도 예측 점수 |")
        lines.append(_md_sep(6))

        # 기사일자 기준으로 정렬 (날짜 내림차순, 동일 날짜 시 위험도 내림차순)
        scored_lawsuits = []
        for s in lawsuits:
            risk_score = calculate_news_risk_score(s.article_title or s.case_title, s.reason)
            scored_lawsuits.append((risk_score, s))
        
        scored_lawsuits.sort(key=lambda x: (x[1].update_or_filed_date or "", x[0]), reverse=True)

        for idx, (risk_score, s) in enumerate(scored_lawsuits, start=1):
            article_url = s.article_urls[0] if getattr(s, "article_urls", None) else ""
            title_cell = _mdlink(s.article_title or s.case_title, article_url)

            lines.append(
                f"| {idx} | "
                f"{_esc(s.update_or_filed_date)} | "
                f"{title_cell} | "
                f"{_esc(s.case_number)} | "
                f"{_short(s.reason)} | "
                f"{format_risk(risk_score)} |"
            )
        lines.append("")
    else:
        lines.append("새로운 소식이 0건입니다.\n")

    # RECAP 케이스
    lines.append("## ⚖️ Cases (Courtlistener+RECAP)")
    if cl_cases:
        
        # CLDocument를 docket_id 기준으로 매핑
        doc_map = {}
        for d in cl_docs:
            if d.docket_id:
                doc_map[d.docket_id] = d
        
        lines.append(
            "| No. | 상태 | 케이스명 | 도켓번호 | Nature | 위험도 | "
            "소송이유 | AI학습관련 핵심주장 | 법적 근거 | 담당판사 | 법원 | "
            "Complaint 문서 번호 | Complaint PDF 링크 | 최근 도켓 업데이트⬇️ |"
        )
        lines.append(_md_sep(14))
        
        # 위험도 점수 기준으로 정렬 (위험도 내림차순, 동일 점수 시 날짜 내림차순)
        scored_cases = []
        for c in cl_cases:
            # 최종 스코어링 소스 텍스트 결정
            ext_causes = c.extracted_causes
            ext_snippet = c.extracted_ai_snippet
            if c.docket_id in doc_map:
                doc = doc_map[c.docket_id]
                ext_causes = doc.extracted_causes or ext_causes
                ext_snippet = doc.extracted_ai_snippet or ext_snippet
            
            # 위험도 계산용 임시 객체 (원본 보호)
            c_copy = copy.copy(c)
            c_copy.extracted_ai_snippet = ext_snippet
            c_copy.extracted_causes = ext_causes
            score = calculate_case_risk_score(c_copy)
            scored_cases.append((score, c, ext_causes, ext_snippet))
            
        scored_cases.sort(key=lambda x: (x[0], x[1].recent_updates if x[1].recent_updates != "미확인" else ""), reverse=True)

        for idx, (score, c, extracted_causes, extracted_ai_snippet) in enumerate(scored_cases, start=1):             
                slug = _slugify_case_name(c.case_name)
                docket_url = f"https://www.courtlistener.com/docket/{c.docket_id}/{slug}/"
      
                complaint_doc_no = c.complaint_doc_no
                complaint_link = c.complaint_link
                
                if c.docket_id in doc_map:
                    doc = doc_map[c.docket_id]
                    complaint_doc_no = doc.doc_number or doc.doc_type
                    complaint_link = doc.document_url or doc.pdf_url
             
                if c.court_short_name and c.court_api_url:
                    court_display = _mdlink(c.court_short_name, c.court_api_url)
                else:
                    court_display = _esc(c.court)

                # =====================================================
                # FIX: Complaint PDF 링크 표시 규칙
                # - 링크 존재 시: 📄 아이콘 출력
                # - 링크 없으면: "-"
                # =====================================================
                if complaint_link:
                    complaint_link_display = _mdlink("📄", complaint_link)
                else:
                    complaint_link_display = "-"

                # =====================================================
                # NEW: RECAP 테이블 로그 출력
                # =====================================================
                debug_log(f"RECAP row added: case={c.case_name}, docket={c.docket_number}, risk={score}")

                # =====================================================
                # NEW: Nature 필드 강조 처리
                # - 820 Copyright → 빨간색 표시
                # =====================================================
                nature_display = _esc(c.nature_of_suit)
                if (c.nature_of_suit or "").strip() == "820 Copyright":
                    nature_display = '⚠️**820 Copyright**'

                lines.append(
                    f"| {idx} | "
                    f"{_esc(c.status)} | "
                    f"{_mdlink(c.case_name, docket_url)} | "
                    f"{_mdlink(c.docket_number, docket_url)} | "
                    f"{nature_display} | "
                    f"{format_risk(score)} | "
                    f"{_short(extracted_causes, 120)} | "
                    f"{_short(extracted_ai_snippet, 120)} | "
                    f"{_esc(c.cause)} | "
                    f"{_esc(c.judge)} | "
                    f"{court_display} | "
                    f"{_esc(complaint_doc_no)} | "
                    f"{complaint_link_display} | "
                    f"{_esc(c.recent_updates)} |"
                )
        lines.append("")
    else:
        lines.append("새로운 소식이 0건입니다.\n")

    # RECAP 법원 문서 (.pdf format)
    if cl_docs:
        lines.append("<details>")        
        lines.append("<summary><strong><span style=\"font-size:2.5em; font-weight:bold;\">📄 Cases: 법원 문서 기반 (Complaint/Petition 우선)</span></strong></summary>\n")
        lines.append("| No. | 제출일⬇️ | 케이스 | 문서유형 | 법원 문서 |")
        lines.append(_md_sep(5))

        # 제출일 기준 내림차순 정렬
        sorted_docs = sorted(
            cl_docs,
            key=lambda x: x.date_filed or "",
            reverse=True
        )

        for idx, d in enumerate(sorted_docs, start=1):
            link = d.document_url or d.pdf_url
            lines.append(
                f"| {idx} | "
                f"{_esc(d.date_filed)} | {_esc(d.case_name)} | "
                f"{_esc(d.doc_type)} | {_mdlink('📄', link)} |"
            )
        lines.append("</details>\n")

    # 기사 주소
    if lawsuits:
        lines.append("<details>")
        lines.append("<summary><strong><span style=\"font-size:2.5em; font-weight:bold;\">📰 News Website</span></strong></summary>\n")
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
    lines.append("-  0~ 39 🟢 : 간접 연관")
    lines.append("- 40~ 59 🟡 : 학습 쟁점 존재")
    lines.append("- 60~ 79 ⚠️ : 모델 학습 직접 언급")
    lines.append("- 80~100 🔥 : 무단 수집 + 학습 + 상업적 사용 고위험")
    lines.append("")

    lines.append("### 🧮 점수 산정 기준")
    lines.append("| 항목 | 조건 (주요 키워드) | 점수 |")
    lines.append("|---|---|---|")
    lines.append("| 무단 데이터 수집 명시 | scrape, crawl, ingest, unauthorized 등 | +30 |")
    lines.append("| 모델 학습 직접 언급 | train, model, llm, generative ai, gpt 등 | +30 |")
    lines.append("| 상업적 사용 | commercial, profit, monetiz, revenue 등 | +15 |")
    lines.append("| 저작권 소송/쟁점 | Nature=820, copyright, infringement, dmca 등 | +15 |")
    lines.append("| 집단소송 | class action, putative class 등 | +10 |")
    lines.append("")

    lines.append("</details>\n")

    return "\n".join(lines) or ""
