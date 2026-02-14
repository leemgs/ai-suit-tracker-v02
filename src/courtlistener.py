from __future__ import annotations

import os
import re
import requests
from dataclasses import dataclass
from typing import List, Dict, Optional
from datetime import datetime, timezone, timedelta

from .pdf_text import extract_pdf_text
from .complaint_parse import (
    detect_causes,
    extract_ai_training_snippet,
    extract_parties_from_caption,
)

BASE = "https://www.courtlistener.com"
STORAGE_BASE = "https://storage.courtlistener.com"

SEARCH_URL = BASE + "/api/rest/v4/search/"
DOCKET_URL = BASE + "/api/rest/v4/dockets/{id}/"
DOCKETS_LIST_URL = BASE + "/api/rest/v4/dockets/"
RECAP_DOCS_URL = BASE + "/api/rest/v4/recap-documents/"
PARTIES_URL = BASE + "/api/rest/v4/parties/"
DOCKET_ENTRIES_URL = BASE + "/api/rest/v4/docket-entries/"

COMPLAINT_KEYWORDS = [
    "complaint",
    "amended complaint",
    "petition",
    "class action complaint",
]

# =====================================================
# Dataclasses
# =====================================================

@dataclass
class CLDocument:
    docket_id: Optional[int]
    docket_number: str
    case_name: str
    court: str
    date_filed: str
    doc_type: str
    doc_number: str
    description: str
    document_url: str
    pdf_url: str
    pdf_text_snippet: str
    extracted_plaintiff: str
    extracted_defendant: str
    extracted_causes: str
    extracted_ai_snippet: str


@dataclass
class CLCaseSummary:
    docket_id: int
    case_name: str
    docket_number: str
    court: str
    court_short_name: str
    court_api_url: str
    date_filed: str
    status: str
    judge: str
    magistrate: str
    nature_of_suit: str
    cause: str
    parties: str
    complaint_doc_no: str
    complaint_link: str
    complaint_type: str
    recent_updates: str
    extracted_causes: str
    extracted_ai_snippet: str
    docket_candidates: str = ""


# =====================================================
# Utility
# =====================================================

def _safe_str(x) -> str:
    return str(x).strip() if x is not None else ""

def _detect_complaint_type(desc: str) -> str:
    d = desc.lower()
    if "second amended" in d:
        return "Second Amended"
    if "third amended" in d:
        return "Third Amended"
    if "amended" in d:
        return "Amended"
    if "class action" in d:
        return "Class Action"
    if "petition" in d:
        return "Petition"
    return "Original"


_court_cache = {}

def _build_court_meta(court_raw: str) -> tuple[str, str]:
    court_raw = _safe_str(court_raw)
    if not court_raw or court_raw == "미확인":
        return "미확인", ""

    # If already full API URL
    if court_raw.startswith("http"):
        court_api_url = court_raw
    elif court_raw.startswith("/"):
        court_api_url = BASE + court_raw
    else:
        # fallback (legacy slug)
        court_api_url = f"{BASE}/api/rest/v4/courts/{court_raw}/"

    if court_api_url in _court_cache:
        return _court_cache[court_api_url], court_api_url

    data = _get(court_api_url)
    if data and data.get("short_name"):
        short_name = data.get("short_name")
        _court_cache[court_api_url] = short_name
        return short_name, court_api_url

    # fallback
    return court_raw, court_api_url


def _headers() -> Dict[str, str]:
    token = os.getenv("COURTLISTENER_TOKEN", "").strip()
    headers = {
        "Accept": "application/json",
        "User-Agent": "ai-lawsuit-monitor/1.4",
    }
    if token:
        headers["Authorization"] = f"Token {token}"
    return headers


def _get(url: str, params: Optional[dict] = None) -> Optional[dict]:
    try:
        print(f"[DEBUG] GET {url}")
        print(f"[DEBUG] PARAMS length={len(str(params)) if params else 0}")

        # 🔥 FIX: CourtListener search는 반드시 GET 사용
        r = requests.get(url, params=params, headers=_headers(), timeout=30)

        if r.status_code in (401, 403):
            print(f"[DEBUG] AUTH ERROR {r.status_code} for {url}")           
            return None

        if r.status_code >= 400:
            print(f"[DEBUG] HTTP ERROR {r.status_code}")
            print(f"[DEBUG] RESPONSE TEXT: {r.text[:500]}")
            return None

        r.raise_for_status()
        print(f"[DEBUG] SUCCESS {url} status={r.status_code}")
        return r.json()
    except Exception as e:
        print(f"[DEBUG] EXCEPTION in _get function: {type(e).__name__}: {e}")    
        return None


def _abs_url(u: str) -> str:
    if not u: return ""
    if u.startswith("http"): return u
    if u.startswith("/"): return BASE + u
    # Critical Fix: RECAP storage uses a different base URL for relative paths
    if u.startswith("pdf/") or u.startswith("gov.uscourts"):
        return "https://storage.courtlistener.com/recap/" + u
    return u


# =====================================================
# NEW: HTML Parsing for PDF (No API Required)
# =====================================================

def _extract_first_pdf_from_docket_html(docket_id: int) -> str:
    """
    Fetch docket HTML page and extract the first PDF link.
    """
    try:
        # 🔥 1. 먼저 API에서 정확한 도켓 URL(slug 포함)을 얻는다
        docket_meta = _get(DOCKET_URL.format(id=docket_id))
        if not docket_meta:
            return ""

        absolute_url = docket_meta.get("absolute_url")
        if not absolute_url:
            return ""

        url = BASE + absolute_url

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
        }

        r = requests.get(url, headers=headers, timeout=25, allow_redirects=True)
        if r.status_code != 200:
            return ""

        html = r.text
        print("[DEBUG] HTML length:", len(html))
        
        # =====================================================
        # 🔥 FIX: 절대 URL + 상대 URL 모두 탐지
        # =====================================================

        # 1️⃣ 절대 URL 먼저 탐지
        match = re.search(
            r"https://storage\.courtlistener\.com/recap/[^\"]+?\.pdf",
            html,
            re.IGNORECASE,
        )
        if match:
            return match.group(0)

        # 2️⃣ 상대 URL 탐지 (/recap/...)
        match = re.search(
            r'href="(/recap/[^"]+?\.pdf)"',
            html,
            re.IGNORECASE,
        )
        if match:
            return STORAGE_BASE + match.group(1)

    except Exception:
        pass

    return ""


# =====================================================
# Search
# =====================================================

def search_recent_documents(query: str, days: int = 3, max_results: int = 50) -> List[dict]:
    print(f"[DEBUG] search_recent_documents query='{query}' days={days}")    
    data = _get(
        SEARCH_URL,
        # 🔥 FIX: RECAP 문서 검색(type=r) → 사건 검색(type=ca)
        # r = recap documents (문서)
        # ca = cases (사건)
        # 사건 기반으로 검색해야 docket_id 확보 가능
        params={
            "q": query,
            "type": "r",                 # 🔥  BEST PRACTICE: 문서 기반 검색 유지
            "order_by": "dateFiled desc",   # 🔥 최신순 정렬            
            "page_size": max_results,
            "semantic": "true",          # 🔥 semantic=true 필수
        },        
    )
    if not data:
        print("[DEBUG] search_recent_documents: no data returned")        
        return []

    results = data.get("results", [])
    print(f"[DEBUG] search results raw count={len(results)}")    
    # 🔥 FIX: 날짜 기준 비교 (시간 제거)
    today = datetime.now(timezone.utc).date()
    cutoff = today - timedelta(days=days)
    print(f"[DEBUG] cutoff date={cutoff}")

    out = []
    for r in results:
        date_val = _safe_str(r.get("dateFiled") or r.get("date_filed"))
        if date_val:
            try:
                dt = datetime.fromisoformat(date_val[:10]).date()
                if dt < cutoff:
                    print(f"[DEBUG] filtered by date: {dt} < {cutoff}")                    
                    continue
            except Exception:
                print(f"[DEBUG] date parse error: {e}")                
                pass
        out.append(r)

    # ✅ BEST PRACTICE: 문서 검색 결과에서 docket_id 안정 확보
    for hit in out:
        if not hit.get("docket_id"):
            docket_url = hit.get("docket")
            if isinstance(docket_url, str):
                m = re.search(r"/dockets/(\d+)/", docket_url)
                if m:
                    hit["docket_id"] = int(m.group(1))
                    print(f"[DEBUG] injected docket_id={hit['docket_id']} from docket URL")

    return out


def _pick_docket_id(hit: dict) -> Optional[int]:
    for key in ["docket_id", "docketId", "docket"]:
        v = hit.get(key)
        if isinstance(v, int):
            print(f"[DEBUG] docket_id found directly: {v}")            
            return v
    # 🔥 NEW: handle string docket URL
    docket_field = hit.get("docket")
    if isinstance(docket_field, str):
        match = re.search(r"/dockets/(\d+)/", docket_field)
        if match:
            did = int(match.group(1))
            print(f"[DEBUG] extracted docket_id from URL: {did}")
            return did
    print("[DEBUG] docket_id not found in hit")
    
    return None


# =====================================================
# Builders
# =====================================================

def build_case_summaries_from_docket_numbers(docket_numbers: List[str]) -> List[CLCaseSummary]:
    out = []
    for dn in docket_numbers:
        data = _get(DOCKETS_LIST_URL, params={"docket_number": dn})
        if not data:
            continue
        for d in data.get("results", []):
            did = d.get("id")
            if did:
                s = build_case_summary_from_docket_id(int(did))
                if s:
                    out.append(s)
    return out


def build_case_summaries_from_case_titles(case_titles: List[str]) -> List[CLCaseSummary]:
    out = []
    for ct in case_titles:
        hits = search_recent_documents(ct, days=365, max_results=5)
        out.extend(build_case_summaries_from_hits(hits))
    return out


def build_case_summaries_from_hits(hits: List[dict]) -> List[CLCaseSummary]:
    out = []
    print(f"[DEBUG] build_case_summaries_from_hits input hits={len(hits)}")    
    for hit in hits:
        did = _pick_docket_id(hit)
        if did:
            print(f"[DEBUG] found docket_id={did}")            
            s = build_case_summary_from_docket_id(did)
            if s:
                out.append(s)
    return out


def build_documents_from_docket_ids(docket_ids: List[int], days: int = 3) -> List[CLDocument]:
    hits = [{"docket_id": did} for did in docket_ids]
    return build_complaint_documents_from_hits(hits)

def build_complaint_documents_from_hits(
    hits: List[dict],
    days: int = 3
) -> List[CLDocument]:

    print(f"[DEBUG] build_complaint_documents_from_hits hits={len(hits)} days={days}")
    out = []

    # 🔥 FIX: 날짜 기준 비교 (시간 제거)
    today = datetime.now(timezone.utc).date()
    cutoff = today - timedelta(days=days)

    for hit in hits:
        did = _pick_docket_id(hit)
        if not did:
            print("[DEBUG] no docket_id in hit")         
            continue

        docket = _get(DOCKET_URL.format(id=did)) or {}
        case_name = _safe_str(docket.get("case_name")) or "미확인"
        docket_number = _safe_str(docket.get("docket_number")) or "미확인"
        court = _safe_str(docket.get("court")) or "미확인"

        print(f"[DEBUG] --- Processing docket {did} ---")
        print(f"[DEBUG] case_name={case_name}")
        print(f"[DEBUG] docket_number={docket_number}")
        print(f"[DEBUG] court={court}")     
        
        # --------------------------------------------------
        # 안정화: docket 전체 기준 RECAP pagination 조회
        # --------------------------------------------------
        docs = []
        url = RECAP_DOCS_URL
        params = {"docket": did, "page_size": 100}
        print(f"[DEBUG] fetching RECAP docs for docket={did}")        

        while url:
            data = _get(url, params=params) if params else _get(url)
            params = None
            if not data:
                print("[DEBUG] RECAP pagination returned no data")                
                break
            docs.extend(data.get("results", []))
            url = data.get("next")
    
        print(f"[DEBUG] total RECAP docs fetched={len(docs)}")
        # 🔥 FIX: initialize fallback variables (avoid NameError / leakage)
        html_pdf_url = ""    

        # =====================================================
        # ✅ BEST PRACTICE: RECAP → HTML fallback
        # =====================================================
        if not docs:
            print("[DEBUG] RECAP empty → HTML fallback activated")
            html_pdf_url = _extract_first_pdf_from_docket_html(did)

            if html_pdf_url:
                snippet = extract_pdf_text(html_pdf_url, max_chars=3000)

                p_ex, d_ex = extract_parties_from_caption(snippet) if snippet else ("미확인", "미확인")
                print(f"[DEBUG] HTML fallback snippet length={len(snippet) if snippet else 0}")                
                causes = detect_causes(snippet) if snippet else []
                ai_snip = extract_ai_training_snippet(snippet) if snippet else ""

                out.append(CLDocument(
                    docket_id=did,
                    docket_number=docket_number,
                    case_name=case_name,
                    court=court,
                    date_filed=_safe_str(docket.get("date_filed"))[:10],
                    doc_type="Complaint (HTML Fallback)",
                    doc_number="1",
                    description="Extracted from docket HTML",
                    document_url=html_pdf_url,
                    pdf_url=html_pdf_url,
                    pdf_text_snippet=snippet,
                    extracted_plaintiff=p_ex,
                    extracted_defendant=d_ex,
                    extracted_causes=", ".join(causes) if causes else "미확인",
                    extracted_ai_snippet=ai_snip,
                ))
            # RECAP 완전 실패한 경우에만 fallback 실행       
        
        for d in docs:
            desc = _safe_str(d.get("description")).lower()
            if not any(k in desc for k in COMPLAINT_KEYWORDS):
                print(f"[DEBUG] skipped non-complaint doc: {desc[:60]}")                
                continue

            date_filed = _safe_str(d.get("date_filed"))[:10]
            if date_filed:
                try:
                    dt = datetime.fromisoformat(date_filed).date()
                    if dt < cutoff:
                        print(f"[DEBUG] complaint filtered by date {dt} < {cutoff}")                        
                        continue
                except Exception:
                    print(f"[DEBUG] complaint date parse error: {e}")                    
                    pass
            print(f"[DEBUG] complaint accepted docket={did} date={date_filed}")
            print(f"[DEBUG] description={d.get('description')}")
            print(f"[DEBUG] document_number={d.get('document_number')}")            
            pdf_url = _abs_url(d.get("filepath_local") or "")
            snippet = extract_pdf_text(pdf_url, max_chars=3000) if pdf_url else ""

            p_ex, d_ex = extract_parties_from_caption(snippet) if snippet else ("미확인", "미확인")
            causes = detect_causes(snippet) if snippet else []
            ai_snip = extract_ai_training_snippet(snippet) if snippet else ""

            out.append(CLDocument(
                docket_id=did,
                docket_number=docket_number,
                case_name=case_name,
                court=court,
                date_filed=date_filed,
                doc_type="Complaint",
                doc_number=_safe_str(d.get("document_number")),
                description=_safe_str(d.get("description")),
                document_url=_abs_url(d.get("absolute_url") or ""),
                pdf_url=pdf_url,
                pdf_text_snippet=snippet,
                extracted_plaintiff=p_ex,
                extracted_defendant=d_ex,
                extracted_causes=", ".join(causes) if causes else "미확인",
                extracted_ai_snippet=ai_snip,
            ))

    return out


def build_case_summary_from_docket_id(docket_id: int) -> Optional[CLCaseSummary]:
    docket = _get(DOCKET_URL.format(id=docket_id))
    if not docket:
        return None
    print(f"[DEBUG] === build_case_summary_from_docket_id {docket_id} ===")
    print(f"[DEBUG] case_name={docket.get('case_name')}")
    print(f"[DEBUG] docket_number={docket.get('docket_number')}")

    case_name = _safe_str(docket.get("case_name")) or "미확인"
    docket_number = _safe_str(docket.get("docket_number")) or "미확인"
    court = _safe_str(docket.get("court")) or "미확인"
    court_short_name, court_api_url = _build_court_meta(court)

    date_filed = _safe_str(docket.get("date_filed"))[:10]
    date_terminated = _safe_str(docket.get("date_terminated"))[:10]

    if date_terminated:
        status = f"종결 ({date_terminated})"
    elif date_filed:
        status = "진행중"
    else:
        status = "미확인"

    judge = (
        _safe_str(docket.get("assigned_to_str"))
        or _safe_str(docket.get("assigned_to"))
        or "미확인"
    )

    magistrate = (
        _safe_str(docket.get("referred_to_str"))
        or _safe_str(docket.get("referred_to"))
        or "미확인"
    )

    nature_of_suit = (
        _safe_str(docket.get("nature_of_suit"))
        or _safe_str(docket.get("nature_of_suit_display"))
        or _safe_str(docket.get("nos"))
        or "미확인"
    )

    cause = (
        _safe_str(docket.get("cause"))
        or _safe_str(docket.get("cause_of_action"))
        or "미확인"
    )

    parties = _safe_str(docket.get("party_summary")) or "미확인"

    recent_updates = (
        _safe_str(docket.get("date_modified"))[:10]
        or _safe_str(docket.get("date_last_filing"))[:10]
        or "미확인"
    )

    # --------------------------------------------------
    # Complaint 찾기 (pagination + PDF 추출)
    # --------------------------------------------------

    complaint_doc_no = "미확인"
    complaint_link = ""
    complaint_type = "미확인"
    extracted_causes = "미확인"
    extracted_ai_snippet = ""    
    
    # ======================================================
    # 🔥 통합 LOGIC
    # RECAP 문서 API 우선 → 없으면 HTML fallback
    # 그리고 결과를 RECAP 테이블 컬럼에 직접 매핑
    # ======================================================

    # 1️⃣ RECAP API 먼저 시도
    recap_docs = []
    url = RECAP_DOCS_URL
    params = {"docket": docket_id, "page_size": 100}

    while url:
        data = _get(url, params=params) if params else _get(url)
        params = None
        if not data:
            break
        recap_docs.extend(data.get("results", []))
        url = data.get("next")

    complaint_doc = None

    for d in recap_docs:
        print(f"[DEBUG] checking RECAP doc: {d.get('description')}")        
        desc = _safe_str(d.get("description")).lower()
        if any(k in desc for k in COMPLAINT_KEYWORDS):
            complaint_doc = d
            break

    # 2️⃣ RECAP 문서가 있으면 사용
    if complaint_doc:
        print("[DEBUG] RECAP complaint document found")

        complaint_doc_no = _safe_str(complaint_doc.get("document_number")) or "1"      
        complaint_link = _abs_url(
            complaint_doc.get("filepath_local")
            or complaint_doc.get("absolute_url")
            or ""
        )
        print(f"[DEBUG] complaint_doc_no={complaint_doc_no}")
        print(f"[DEBUG] complaint_link={complaint_link}")
        complaint_type = _detect_complaint_type(_safe_str(complaint_doc.get("description")))

    # 3️⃣ 없으면 HTML fallback
    if not complaint_link:
        print("[DEBUG] RECAP complaint not found → HTML fallback attempt")
        
        html_pdf_url = _extract_first_pdf_from_docket_html(docket_id)
        if html_pdf_url:
            print(f"[DEBUG] HTML fallback PDF found: {html_pdf_url}")            
            complaint_link = html_pdf_url
            complaint_doc_no = "1"
            complaint_type = "Complaint (HTML Fallback)"
        else:
            print("[DEBUG] HTML fallback failed — no PDF found")

        print(f"[DEBUG] final complaint_link={complaint_link}")
    
    # 4️⃣ PDF 텍스트 분석
    if complaint_link:
        print(f"[DEBUG] Extracting PDF text from: {complaint_link}")        
        snippet = extract_pdf_text(complaint_link, max_chars=4000)

        print(f"[DEBUG] PDF snippet length={len(snippet) if snippet else 0}")

        if snippet:
            print("[DEBUG] ===== PDF TEXT PREVIEW BEGIN =====")
            print(snippet[:1000])
            print("[DEBUG] ===== PDF TEXT PREVIEW END =====")
        else:
            print("[DEBUG] PDF text extraction returned EMPTY STRING")
        
        print(f"[DEBUG] PDF snippet length={len(snippet) if snippet else 0}")        
        if snippet:
            extracted_ai_snippet = extract_ai_training_snippet(snippet) or ""
            causes_list = detect_causes(snippet)
            print(f"[DEBUG] extracted_ai_snippet length={len(extracted_ai_snippet)}")
            print(f"[DEBUG] detected causes={causes_list}")            
            extracted_causes = ", ".join(causes_list) if causes_list else "미확인"
        else:
            print("[DEBUG] WARNING: PDF text extraction returned empty snippet")
    else:
        print("[DEBUG] No complaint_link available — skipping PDF extraction")

    return CLCaseSummary(
        docket_id=docket_id,
        case_name=case_name,
        docket_number=docket_number,
        court=court,
        court_short_name=court_short_name,
        court_api_url=court_api_url,
        date_filed=date_filed or "미확인",
        status=status,
        judge=judge,
        magistrate=magistrate,
        nature_of_suit=nature_of_suit,
        cause=cause,
        parties=parties,
        complaint_doc_no=complaint_doc_no,
        complaint_link=complaint_link,
        complaint_type=complaint_type,
        recent_updates=recent_updates,
        extracted_causes=extracted_causes,
        extracted_ai_snippet=extracted_ai_snippet,
    )

