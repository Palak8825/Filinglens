"""
Auditor events → Fact(kind="auditor_event") rows. Rules only, no LLM: every event points at a
filing page on disk, so the flag can be cited and checked.

Events
  resignation             statutory auditor of the listed company resigned / casual vacancy
  subsidiary_resignation  statutory auditor of a (material) subsidiary resigned
  qualified_opinion       an audit report in a results filing carries an SA 705 modified opinion
                          (qualified / adverse / disclaimer)
Ignored on purpose: internal, cost and secretarial auditor changes, and scheduled rotations
(e.g. RBI-mandated changes of bank central statutory auditors) — routine, not red flags.

    python -m ingest.auditor --symbols RELINFRA,PCJEWELLER,EASEMYTRIP
"""
from __future__ import annotations

import argparse
import csv
import re
from datetime import date
from pathlib import Path

import fitz
from sqlalchemy import select

from core.db import Document, Fact, get_session, init_db
from ingest.announcements import Announcement, read_manifest
from ingest.parse_pdf import local_path

UNIVERSE = Path(__file__).resolve().parent.parent / "scripts" / "universe.csv"

AUDITOR_TITLE = re.compile(r"auditor|casual vacancy", re.I)
NOT_STATUTORY = re.compile(r"\b(internal|cost|secretarial|tax|branch|concurrent)\s+auditor", re.I)
RESIGN = re.compile(r"resign\w*|casual\s+vacancy", re.I)
# Every SEBI Reg-30 annexure contains this boilerplate, even for a routine appointment.
TEMPLATE = re.compile(r"reasons?\s+for\s+change\s*,?\s*viz\.?,?\s*appointment.{0,90}?otherwise", re.I | re.S)
PERSON = re.compile(r"\b(director|DIN|KMP|key\s+managerial|chief|officer|company\s+secretary)\b", re.I)
STATUTORY = re.compile(r"statutory\s+(central\s+)?auditor|auditors?\s+of\s+the\s+company|joint\s+auditor", re.I)
SUBSIDIARY = re.compile(r"subsidiar", re.I)
# SA 705 headings. They appear only in modified reports — never in clean opinions or in the blank
# "Statement on Impact of Audit Qualifications" template, which lists the options without them.
MODIFIED = re.compile(r"basis\s+for\s+(qualified|adverse)\s+opinion|basis\s+for\s+disclaimer\s+of\s+opinion", re.I)


def _pages(path: str, max_pages: int | None = None) -> list[tuple[int, str]]:
    out = []
    with fitz.open(local_path(path)) as doc:
        for i, page in enumerate(doc, start=1):
            if max_pages and i > max_pages:
                break
            out.append((i, page.get_text("text")))
    return out


def _snippet(text: str, m: re.Match, width: int = 220) -> str:
    s = max(0, m.start() - width // 2)
    return re.sub(r"\s+", " ", text[s:s + width]).strip()


def classify_announcement(ann: Announcement) -> dict | None:
    """Return an event dict for a statutory-auditor resignation, else None."""
    title = re.sub(r"\s+", " ", ann.title)
    if not AUDITOR_TITLE.search(title):
        return None
    if NOT_STATUTORY.search(title) and not STATUTORY.search(title):
        return None
    def event(page_no: int, text: str, m: re.Match, window: str) -> dict:
        # "subsidiary" must sit right around the resignation, not anywhere on the page.
        near = text[max(0, m.start() - 200): m.end() + 60]
        sub = bool(SUBSIDIARY.search(near) or SUBSIDIARY.search(title))
        return {
            "event": "subsidiary_resignation" if sub else "resignation",
            "page": page_no,
            "summary": f"{'Subsidiary s' if sub else 'S'}tatutory auditor resignation / casual vacancy, filed {ann.filed_on}: {title[:160]}",
            "quote": _snippet(text, m),
        }

    # Title alone is decisive when it says so outright ("Resignation of Statutory Auditor").
    tm = RESIGN.search(title)
    if tm and STATUTORY.search(title) and not NOT_STATUTORY.search(title) and not PERSON.search(title):
        return event(1, title, tm, title)
    try:
        pages = _pages(ann.path, max_pages=4)
    except Exception:  # noqa: BLE001 - unreadable PDF
        return None
    for page_no, raw in pages:
        text = TEMPLATE.sub(" ", raw)
        for m in RESIGN.finditer(text):
            window = text[max(0, m.start() - 250): m.end() + 250]
            if not STATUTORY.search(window) or NOT_STATUTORY.search(window):
                continue
            if PERSON.search(text[m.start(): m.end() + 120]):
                continue  # "resignation of Mr. X, Director" next to an auditor item
            return event(page_no, text, m, window)
    return None


def scan_modified_opinion(ann: Announcement) -> dict | None:
    try:
        pages = _pages(ann.path)
    except Exception:  # noqa: BLE001
        return None
    for page_no, text in pages:
        m = MODIFIED.search(text)
        if m:
            kind = m.group(0).lower()
            label = "adverse" if "adverse" in kind else "disclaimer" if "disclaimer" in kind else "qualified"
            return {
                "event": "qualified_opinion",
                "opinion": label,
                "page": page_no,
                "summary": f"Auditor issued a {label} opinion (results filed {ann.filed_on}): {ann.title[:120]}",
                "quote": _snippet(text, m, 300),
            }
    return None


def build_facts(symbol: str, anns: list[Announcement]) -> list[Fact]:
    mine = [a for a in anns if a.symbol == symbol and a.path and local_path(a.path).exists()]
    with get_session() as s:
        # Link to the indexed Document when there is one, so the UI can render the cited page.
        doc_ids = {p: i for p, i in s.execute(select(Document.path, Document.id).where(Document.symbol == symbol)).all()}
    facts: list[Fact] = []
    seen: set[tuple[str, str]] = set()
    last_resign: date | None = None
    for a in sorted(mine, key=lambda x: x.filed_on):
        ev = classify_announcement(a) if a.doc_type == "announcement" else scan_modified_opinion(a) if a.doc_type == "results" else None
        if not ev:
            continue
        filed = date.fromisoformat(a.filed_on)
        if ev["event"] != "qualified_opinion":
            # A resignation is followed by audit-committee views, the casual-vacancy appointment,
            # corrigenda... — one episode, keep its first filing.
            if last_resign and (filed - last_resign).days <= 60:
                continue
            last_resign = filed
        key = (ev["event"], a.filed_on[:7])  # one event per type per month (filers often send duplicates)
        if key in seen:
            continue
        seen.add(key)
        facts.append(Fact(
            symbol=symbol, kind="auditor_event", period=None, as_of=date.fromisoformat(a.filed_on),
            document_id=doc_ids.get(a.path), page=ev["page"],
            payload={**ev, "title": a.title[:300], "path": a.path, "source_url": a.source_url, "filed_on": a.filed_on},
        ))
    return facts


def ingest_auditor(symbols: list[str]) -> None:
    init_db()
    anns = read_manifest()
    for sym in symbols:
        facts = build_facts(sym, anns)
        with get_session() as s:
            s.query(Fact).filter(Fact.symbol == sym, Fact.kind == "auditor_event").delete()
            s.add_all(facts)
            s.commit()
        detail = "; ".join(f"{f.payload['event']} {f.payload['filed_on']} p{f.page}" for f in facts)
        print(f"== {sym}: {len(facts)} event(s) {detail}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--symbols", help="comma-separated; default: whole universe")
    a = p.parse_args()
    if a.symbols:
        syms = [x.strip().upper() for x in a.symbols.split(",")]
    else:
        with UNIVERSE.open(encoding="utf-8") as f:
            syms = [r["symbol"] for r in csv.DictReader(f)]
    ingest_auditor(syms)
