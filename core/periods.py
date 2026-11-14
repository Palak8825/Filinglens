"""
Fiscal-period helpers shared by ingestion and retrieval. Kept dependency-free (stdlib only) so the
query path (core/retrieve.py → API) never imports the ingestion stack (pymupdf, the BSE fetcher).
"""
from __future__ import annotations

import re
from datetime import date

QUARTERLY = {"results", "concall", "shareholding"}


def infer_fiscal_period(doc_type: str, filed_on: date) -> str | None:
    """Indian FY ends 31 Mar. Quarterly filings describe the last completed quarter:
    filed Jul-Sep → Q1, Oct-Dec → Q2, Jan-Mar → Q3, Apr-Jun → Q4 of the FY that just ended.
    Annual reports (filed Jun-Sep) describe the FY that ended the previous March."""
    m, y = filed_on.month, filed_on.year
    fy_end = y + 1 if m >= 4 else y  # FY label = calendar year in which that FY ends
    if doc_type in QUARTERLY:
        if 7 <= m <= 9:
            return f"Q1FY{fy_end % 100:02d}"
        if 10 <= m <= 12:
            return f"Q2FY{fy_end % 100:02d}"
        if 1 <= m <= 3:
            return f"Q3FY{fy_end % 100:02d}"
        return f"Q4FY{(fy_end - 1) % 100:02d}"  # Apr-Jun: results for the FY that just closed
    if doc_type == "annual_report":
        return f"FY{(y if m >= 4 else y - 1) % 100:02d}"
    return None


def context_header(symbol: str, doc_type: str, fiscal_period: str | None, title: str) -> str:
    """Prepended to chunk text for embedding/BM25 only (never stored), so retrieval can tell
    Q2 FY27 from Q1 FY27 even when the chunk itself just says "this quarter"."""
    period = re.sub(r"^(Q[1-4])(FY\d{2})$", r"\1 \2", fiscal_period or "")
    return f"{symbol} {doc_type.replace('_', ' ')} {period} | {title[:120]}".strip()
