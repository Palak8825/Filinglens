"""
NSE/BSE corporate announcements → local PDFs + metadata.

PORT TARGET: replace the bodies of fetch_bse_announcements / fetch_nse_announcements with the
polling + download logic from finstrats pipeline/qrd/. Keep the return shape below.

Everything downloaded is written to data/raw/<SYMBOL>/<doc_type>/<filed_on>_<slug>.pdf and
recorded in data/raw/manifest.jsonl so the rest of the pipeline never touches the network.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Iterator

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from core.config import settings

MANIFEST = settings.data_dir / "raw" / "manifest.jsonl"

# Map announcement subjects → our doc_type. Extend as you see real subjects.
SUBJECT_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"transcript", re.I), "concall"),
    (re.compile(r"annual report", re.I), "annual_report"),
    (re.compile(r"(financial results|unaudited|audited results)", re.I), "results"),
    (re.compile(r"(shareholding pattern|pledge|encumbrance)", re.I), "shareholding"),
    (re.compile(r"(auditor|resignation|appointment)", re.I), "announcement"),
]


@dataclass
class Announcement:
    symbol: str
    doc_type: str
    title: str
    filed_on: str  # ISO date
    source_url: str
    path: str = ""
    fiscal_period: str | None = None


# Filings whose attachment is just a cover letter / link, not content worth indexing.
SKIP_RE = re.compile(r"(audio recording|video recording|intimation of (the )?(earnings|analyst|investor) (call|meet)|newspaper (advertisement|publication))", re.I)


def classify(subject: str) -> str | None:
    if SKIP_RE.search(subject):
        return None
    for pat, dtype in SUBJECT_RULES:
        if pat.search(subject):
            return dtype
    return None


HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json, text/plain, */*"}


@retry(stop=stop_after_attempt(6), wait=wait_exponential(min=2, max=60), reraise=True)
def _get(url: str, headers: dict | None = None, **kw) -> requests.Response:
    r = requests.get(url, headers=headers or HEADERS, timeout=30, **kw)
    r.raise_for_status()
    return r


def fetch_bse_announcements(scrip_code: str, symbol: str, start: date, end: date) -> Iterator[Announcement]:
    """
    BSE announcement API. Tolerant of scraping; prefer this over NSE.
    TODO(port): drop in finstrats BSE poller here.
    """
    # BSE returns 400 for any window longer than ~1 year, so walk the range in 360-day chunks.
    win_start = start
    while win_start <= end:
        win_end = min(win_start + timedelta(days=359), end)
        yield from _fetch_bse_window(scrip_code, symbol, win_start, win_end)
        win_start = win_end + timedelta(days=1)


def _fetch_bse_window(scrip_code: str, symbol: str, start: date, end: date) -> Iterator[Announcement]:
    url = "https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w"
    params = {
        "pageno": 1, "strCat": "-1", "strPrevDate": start.strftime("%Y%m%d"),
        "strScrip": scrip_code, "strSearch": "P", "strToDate": end.strftime("%Y%m%d"), "strType": "C", "subcategory": "-1",
    }
    headers = {**HEADERS, "Referer": "https://www.bseindia.com/"}
    fetched = 0
    while True:
        data = _get(url, params=params, headers=headers).json()
        rows = data.get("Table", [])
        if not rows:
            break
        fetched += len(rows)
        total = (data.get("Table1") or [{}])[0].get("ROWCNT")
        for r in rows:
            headline = (r.get("HEADLINE") or r.get("NEWSSUB") or "").strip()
            dtype = classify(headline + " " + (r.get("SUBCATNAME") or ""))
            if not dtype or not r.get("ATTACHMENTNAME") or not r.get("NEWS_DT"):
                continue
            yield Announcement(
                symbol=symbol, doc_type=dtype, title=headline,
                filed_on=r["NEWS_DT"][:10],
                source_url=f"https://www.bseindia.com/xml-data/corpfiling/AttachLive/{r['ATTACHMENTNAME']}",
            )
        if total is not None and fetched >= int(total):
            break
        params["pageno"] += 1
        time.sleep(0.5)


def fetch_nse_announcements(symbol: str, start: date, end: date) -> Iterator[Announcement]:
    """
    NSE needs a cookie bootstrap and breaks often. Only use as a fallback.
    TODO(port): drop in finstrats NSE poller here.
    """
    s = requests.Session()
    s.headers.update(HEADERS)
    s.get("https://www.nseindia.com", timeout=30)  # cookie bootstrap
    url = "https://www.nseindia.com/api/corporate-announcements"
    params = {"index": "equities", "symbol": symbol, "from_date": start.strftime("%d-%m-%Y"), "to_date": end.strftime("%d-%m-%Y")}
    r = s.get(url, params=params, timeout=30)
    r.raise_for_status()
    for row in r.json():
        dtype = classify(row.get("desc", "") + " " + row.get("attchmntText", ""))
        if not dtype or not row.get("attchmntFile"):
            continue
        yield Announcement(symbol=symbol, doc_type=dtype, title=row["desc"].strip(),
                           filed_on=row["an_dt"][:10], source_url=row["attchmntFile"])


def _candidate_urls(url: str) -> list[str]:
    # BSE moves older attachments from AttachLive to AttachHis; try both.
    if "/AttachLive/" in url:
        return [url, url.replace("/AttachLive/", "/AttachHis/")]
    if "/AttachHis/" in url:
        return [url, url.replace("/AttachHis/", "/AttachLive/")]
    return [url]


def download(ann: Announcement) -> Announcement:
    slug = re.sub(r"[^a-z0-9]+", "-", ann.title.lower())[:60].strip("-")
    # Suffix with a short hash of the source URL so two filings with the same date+title don't collide.
    tag = hashlib.sha1(ann.source_url.encode()).hexdigest()[:8]
    out = settings.data_dir / "raw" / ann.symbol / ann.doc_type / f"{ann.filed_on}_{slug}_{tag}.pdf"
    out.parent.mkdir(parents=True, exist_ok=True)
    if not out.exists():
        headers = {**HEADERS, "Referer": "https://www.bseindia.com/"}
        last_err: Exception | None = None
        for u in _candidate_urls(ann.source_url):
            try:
                r = requests.get(u, headers=headers, timeout=60)
                if r.status_code == 200 and r.content[:5] == b"%PDF-":
                    out.write_bytes(r.content)
                    ann.source_url = u
                    break
                last_err = RuntimeError(f"HTTP {r.status_code}, not a PDF")
            except requests.RequestException as e:
                last_err = e
        else:
            raise RuntimeError(f"download failed: {last_err}")
    ann.path = str(out)
    return ann


def append_manifest(ann: Announcement) -> None:
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    with MANIFEST.open("a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(ann), ensure_ascii=False) + "\n")


def read_manifest() -> list[Announcement]:
    if not MANIFEST.exists():
        return []
    return [Announcement(**json.loads(line)) for line in MANIFEST.read_text(encoding="utf-8").splitlines() if line.strip()]


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("--symbol", required=True)
    p.add_argument("--bse-code", required=True)
    p.add_argument("--start", default="2023-04-01")
    p.add_argument("--end", default=str(date.today()))
    a = p.parse_args()
    for ann in fetch_bse_announcements(a.bse_code, a.symbol, date.fromisoformat(a.start), date.fromisoformat(a.end)):
        try:
            append_manifest(download(ann))
            print("ok ", ann.doc_type, ann.filed_on, ann.title[:70])
        except Exception as e:  # noqa: BLE001
            print("ERR", ann.title[:70], e)
