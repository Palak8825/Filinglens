"""
NSE/BSE corporate announcements → local PDFs + metadata.

PORT TARGET: replace the bodies of fetch_bse_announcements / fetch_nse_announcements with the
polling + download logic from finstrats pipeline/qrd/. Keep the return shape below.

Everything downloaded is written to data/raw/<SYMBOL>/<doc_type>/<filed_on>_<slug>.pdf and
recorded in data/raw/manifest.jsonl so the rest of the pipeline never touches the network.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import date
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


def classify(subject: str) -> str | None:
    for pat, dtype in SUBJECT_RULES:
        if pat.search(subject):
            return dtype
    return None


HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json, text/plain, */*"}


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8))
def _get(url: str, **kw) -> requests.Response:
    r = requests.get(url, headers=HEADERS, timeout=30, **kw)
    r.raise_for_status()
    return r


def fetch_bse_announcements(scrip_code: str, symbol: str, start: date, end: date) -> Iterator[Announcement]:
    """
    BSE announcement API. Tolerant of scraping; prefer this over NSE.
    TODO(port): drop in finstrats BSE poller here.
    """
    url = "https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w"
    params = {
        "pageno": 1, "strCat": "-1", "strPrevDate": start.strftime("%Y%m%d"),
        "strScrip": scrip_code, "strSearch": "P", "strToDate": end.strftime("%Y%m%d"), "strType": "C", "subcategory": "-1",
    }
    headers = {**HEADERS, "Referer": "https://www.bseindia.com/"}
    while True:
        data = _get(url, params=params, headers=headers).json()
        rows = data.get("Table", [])
        if not rows:
            break
        for r in rows:
            dtype = classify(r.get("HEADLINE", "") + " " + r.get("SUBCATNAME", ""))
            if not dtype or not r.get("ATTACHMENTNAME"):
                continue
            yield Announcement(
                symbol=symbol, doc_type=dtype, title=r["HEADLINE"].strip(),
                filed_on=r["NEWS_DT"][:10],
                source_url=f"https://www.bseindia.com/xml-data/corpfiling/AttachLive/{r['ATTACHMENTNAME']}",
            )
        params["pageno"] += 1


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


def download(ann: Announcement) -> Announcement:
    slug = re.sub(r"[^a-z0-9]+", "-", ann.title.lower())[:60].strip("-")
    out = settings.data_dir / "raw" / ann.symbol / ann.doc_type / f"{ann.filed_on}_{slug}.pdf"
    out.parent.mkdir(parents=True, exist_ok=True)
    if not out.exists():
        out.write_bytes(_get(ann.source_url, headers={**HEADERS, "Referer": "https://www.bseindia.com/"}).content)
    ann.path = str(out)
    return ann


def append_manifest(ann: Announcement) -> None:
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    with MANIFEST.open("a") as f:
        f.write(json.dumps(asdict(ann)) + "\n")


def read_manifest() -> list[Announcement]:
    if not MANIFEST.exists():
        return []
    return [Announcement(**json.loads(line)) for line in MANIFEST.read_text().splitlines() if line.strip()]


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
