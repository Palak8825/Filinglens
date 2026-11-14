"""
Quarterly shareholding pattern → Fact(kind="shareholding") rows.

Source: NSE. `corporate-share-holdings-master` lists every quarterly filing with promoter % and a
link to the filing's XBRL; the XBRL carries promoter-group encumbrance broken into pledge,
non-disposal undertakings (NDU) and other encumbrances. Structured data, so no PDF table parsing.

Every XBRL is cached under data/raw/<SYMBOL>/shareholding_xbrl/ so a re-run is offline and the
corpus stays frozen. Each Fact keeps the XBRL URL so a flag can cite the exact filing.

    python -m ingest.shareholding --symbols VEDL,ZEEL      # fetch (or reuse cache) + write facts
    python -m ingest.shareholding --symbols VEDL --offline # cache only, no network
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import time
from datetime import date, datetime
from pathlib import Path

import requests

from core.config import settings
from core.db import Fact, get_session, init_db

NSE = "https://www.nseindia.com"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
# Pre-2025 filings use "...GroupI"; the 2025 taxonomy uses "...Group_ContextI".
PROMOTER_CTXS = ("ShareholdingOfPromoterAndPromoterGroup_ContextI", "ShareholdingOfPromoterAndPromoterGroupI")
QUARTER_ENDS = {(3, 31), (6, 30), (9, 30), (12, 31)}
UNIVERSE = Path(__file__).resolve().parent.parent / "scripts" / "universe.csv"


def _session(symbol: str) -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept": "application/json, text/plain, */*", "Accept-Language": "en-US,en;q=0.9",
                      "Referer": f"{NSE}/get-quotes/equity?symbol={symbol}"})
    s.get(NSE, timeout=30)  # Akamai cookie bootstrap
    return s


def _cache_dir(symbol: str) -> Path:
    return settings.data_dir / "raw" / symbol / "shareholding_xbrl"


def fetch_index(symbol: str, s: requests.Session | None, offline: bool = False) -> list[dict]:
    """Quarter list from NSE (cached as index.json)."""
    path = _cache_dir(symbol) / "index.json"
    if offline or (path.exists() and time.time() - path.stat().st_mtime < 86400):
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    assert s is not None
    r = s.get(f"{NSE}/api/corporate-share-holdings-master", params={"index": "equities", "symbol": symbol}, timeout=30)
    r.raise_for_status()
    rows = r.json()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    return rows


def fetch_xbrl(symbol: str, as_of: date, url: str, s: requests.Session | None, offline: bool = False) -> str | None:
    path = _cache_dir(symbol) / f"{as_of.isoformat()}.xml"
    if path.exists():
        return path.read_text(encoding="utf-8", errors="replace")
    if offline or not url:
        return None
    assert s is not None
    r = s.get(url, timeout=60)
    if r.status_code != 200 or "<xbrl" not in r.text[:2000].lower():
        return None
    path.write_text(r.text, encoding="utf-8")
    time.sleep(0.7)
    return r.text


def _num(xml: str, element: str, ctx: str) -> float | None:
    m = re.search(rf'<in-bse-shp:{element}\b[^>]*contextRef="{ctx}"[^>]*>([^<]*)<', xml)
    if not m:
        return None
    try:
        return float(m.group(1).strip())
    except ValueError:
        return None


def _flag(xml: str, element: str) -> bool | None:
    m = re.search(rf"<in-bse-shp:{element}\b[^>]*>([^<]*)<", xml)
    return None if not m else m.group(1).strip().lower() == "true"


def parse_encumbrance(xml: str) -> dict:
    """Promoter-group encumbrance as % of promoter holding.

    Percentages are computed from share COUNTS, never read from the filing's % fields: filers
    mix 0-1 fractions and 0-100 percentages for the same element (VEDL Q1FY26 vs Q2FY26).
    Handles three shapes: pre-2025 (single "PledgedOrEncumbered" figure, no split), 2025+
    (pledge / NDU / other split), and 2025+ with the elements omitted because nothing is encumbered.
    """
    ctx = next((c for c in PROMOTER_CTXS if f'contextRef="{c}"' in xml), None)
    if ctx is None:
        return {}
    promoter_shares = _num(xml, "NumberOfShares", ctx) or _num(xml, "NumberOfFullyPaidUpEquityShares", ctx)
    if not promoter_shares:
        return {}

    def pct(n: float | None) -> float | None:
        return None if n is None else round(100.0 * n / promoter_shares, 2)

    out: dict = {"promoter_shares": int(promoter_shares)}
    legacy = _num(xml, "PledgedOrEncumberedNumberOfShares", ctx)
    if legacy is not None:  # pre-2025: pledge and other encumbrances reported together
        out.update(encumbered_pct_of_promoter=pct(legacy), pledged_pct_of_promoter=None,
                   encumbrance_split_available=False)
        return out
    total = _num(xml, "NumberOfSharesEncumbered", ctx)
    if total is None:
        flags = [_flag(xml, e) for e in ("WhetherAnySharesHeldByPromotersAreEncumberedUnderPledged",
                                          "WhetherAnySharesHeldByPromotersAreEncumberedUnderNonDisposalUndertaking",
                                          "WhetherAnySharesHeldByPromotersAreEncumberedOtherThanByWayOfPledgeOrNDU")]
        if any(f is None for f in flags) or any(flags):
            return out  # can't tell; leave encumbrance unknown rather than claim zero
        total = 0.0
    ndu = _num(xml, "NumberOfSharesEncumberedUnderNonDisposalUndertaking", ctx) or 0.0
    other = _num(xml, "NumberOfSharesEncumberedUnderOtherEncumbrances", ctx) or 0.0
    out.update(
        encumbered_pct_of_promoter=pct(total),
        pledged_pct_of_promoter=pct(max(total - ndu - other, 0.0)),
        ndu_pct_of_promoter=pct(ndu),
        other_encumbrance_pct_of_promoter=pct(other),
        encumbrance_split_available=True,
    )
    return out


def _period(as_of: date) -> str:
    q = {6: 1, 9: 2, 12: 3, 3: 4}[as_of.month]
    fy = as_of.year + 1 if as_of.month >= 4 else as_of.year
    return f"Q{q}FY{fy % 100:02d}"


def build_facts(symbol: str, quarters: int = 12, offline: bool = False) -> list[Fact]:
    s = None if offline else _session(symbol)
    rows = fetch_index(symbol, s, offline)
    by_date: dict[date, dict] = {}
    for r in rows:
        try:
            d = datetime.strptime(r["date"], "%d-%b-%Y").date()
        except (KeyError, ValueError):
            continue
        if (d.month, d.day) not in QUARTER_ENDS:
            continue  # ad-hoc filings (e.g. pre-scheme snapshots) aren't quarterly patterns
        prev = by_date.get(d)
        if prev is None or str(r.get("submissionDate", "")) > str(prev.get("submissionDate", "")):
            by_date[d] = r  # keep the latest (revised) filing for that quarter
    facts: list[Fact] = []
    for d in sorted(by_date)[-quarters:]:
        r = by_date[d]
        xml = fetch_xbrl(symbol, d, r.get("xbrl", ""), s, offline)
        enc = parse_encumbrance(xml) if xml else {}
        try:
            promoter = float(r["pr_and_prgrp"])
        except (KeyError, TypeError, ValueError):
            continue
        payload = {
            "promoter_pct": promoter,
            "public_pct": float(r["public_val"]) if r.get("public_val") not in (None, "") else None,
            **enc,
            "source": "NSE shareholding pattern (XBRL)",
            "source_url": r.get("xbrl"),
            "filed_on": r.get("submissionDate"),
            "notes": (r.get("remarksWeb") or "")[:600] if (r.get("remarksWeb") or "N") != "N" else "",
            "method": "nse_xbrl" if enc else "nse_index_only",
        }
        facts.append(Fact(symbol=symbol, kind="shareholding", period=_period(d), as_of=d, payload=payload))
    return facts


def ingest_shareholding(symbols: list[str], offline: bool = False) -> None:
    init_db()
    for sym in symbols:
        facts = None
        for attempt in range(3):  # NSE resets connections under load; re-bootstrap and retry
            try:
                facts = build_facts(sym, offline=offline)
                break
            except Exception as e:  # noqa: BLE001
                print(f"  retry {attempt + 1}/3 {sym}: {type(e).__name__}: {str(e)[:120]}")
                time.sleep(5 * (attempt + 1))
        if facts is None:
            print(f"  ERR {sym}: giving up")
            continue
        with get_session() as s:  # replace, so re-runs are idempotent
            s.query(Fact).filter(Fact.symbol == sym, Fact.kind == "shareholding").delete()
            s.add_all(facts)
            s.commit()
        if facts:
            a, b = facts[0].payload, facts[-1].payload
            print(f"== {sym}: {len(facts)} quarters {facts[0].period}..{facts[-1].period} | promoter "
                  f"{a['promoter_pct']:.2f}% -> {b['promoter_pct']:.2f}% | encumbered "
                  f"{a.get('encumbered_pct_of_promoter', '?')}% -> {b.get('encumbered_pct_of_promoter', '?')}% | pledged "
                  f"{a.get('pledged_pct_of_promoter', '?')}% -> {b.get('pledged_pct_of_promoter', '?')}%")
        else:
            print(f"== {sym}: no quarters")
        time.sleep(1.0)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--symbols", help="comma-separated; default: whole universe")
    p.add_argument("--offline", action="store_true", help="use cached XBRL only")
    a = p.parse_args()
    if a.symbols:
        syms = [x.strip().upper() for x in a.symbols.split(",")]
    else:
        with UNIVERSE.open(encoding="utf-8") as f:
            syms = [r["symbol"] for r in csv.DictReader(f)]
    ingest_shareholding(syms, a.offline)
