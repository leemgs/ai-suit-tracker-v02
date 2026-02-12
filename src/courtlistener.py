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
        r = requests.get(url, params=params, headers=_headers(), timeout=25)
        if r.status_code in (401, 403):
            return None
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


def _abs_url(u: str) -> str:
    if not u:
        return ""
    if u.startswith("http"):
        return u
    if u.startswith("/"):
        return BASE + u
    return u


# =====================================================
# Search
# =====================================================

def search_recent_documents(query: str, days: int = 3, max_results: int = 20) -> List[dict]:
    data = _get(
        SEARCH_URL,
        params={"q": query, "type": "r", "page_size": max_results},
    )
    if not data:
        return []

    results = data.get("results", [])
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)

    out = []
    for r in results:
        date_val = _safe_str(r.get("dateFiled") or r.get("date_filed"))
        if date_val:
            try:
                dt = datetime.fromisoformat(date_val[:10]).replace(tzinfo=timezone.utc)
                if dt < cutoff:
                    continue
            except Exception:
                pass
        out.append(r)

    return out


def _pick_docket_id(hit: dict) -> Optional[int]:
    for key in ["docket_id", "docketId", "docket"]:
        v = hit.get(key)
        if isinstance(v, int):
            return v
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
    for hit in hits:
        did = _pick_docket_id(hit)
        if did:
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

    out = []

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)

    for hit in hits:
        did = _pick_docket_id(hit)
        if not did:
            continue

        docket = _get(DOCKET_URL.format(id=did)) or {}
        case_name = _safe_str(docket.get("case_name")) or "미확인"
        docket_number = _safe_str(docket.get("docket_number")) or "미확인"
        court = _safe_str(docket.get("court")) or "미확인"

        recap = _get(RECAP_DOCS_URL, params={"docket": did}) or {}
        docs = recap.get("results", [])

        for d in docs:
            desc = _safe_str(d.get("description")).lower()
            if not any(k in desc for k in COMPLAINT_KEYWORDS):
                continue

            date_filed = _safe_str(d.get("date_filed"))[:10]
            if date_filed:
                try:
                    dt = datetime.fromisoformat(date_filed).replace(tzinfo=timezone.utc)
                    if dt < cutoff:
                        continue
                except Exception:
                    pass

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

    url = DOCKET_ENTRIES_URL
    params = {"docket": docket_id}
    params = {"docket": docket_id, "page_size": 100}

    complaints = []

    while url:
        data = _get(url, params=params) if params else _get(url)
        params = None
        if not data:
            break

        for e in data.get("results", []):
            desc = _safe_str(e.get("description")).lower()

            if any(k in desc for k in COMPLAINT_KEYWORDS):
                complaints.append(e)

        url = data.get("next")
        
    if complaints:
        complaints.sort(
            key=lambda x: _safe_str(x.get("date_filed")),
            reverse=True
        )

        latest = complaints[0]

        complaint_doc_no = _safe_str(latest.get("entry_number")) or "미확인"
        desc_text = _safe_str(latest.get("description"))
        complaint_type = _detect_complaint_type(desc_text)

        entry_id = latest.get("id")

        recap = _get(RECAP_DOCS_URL, params={"docket_entry": entry_id}) or {}
        docs = recap.get("results", [])

        if docs:
            pdf_path = docs[0].get("filepath_local") or ""
            complaint_link = _abs_url(pdf_path)

        if not complaint_link:
            complaint_link = _abs_url(latest.get("absolute_url") or "")
    
    # --------------------------------------------------

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
        extracted_causes="미확인",
        extracted_ai_snippet="",
    )

