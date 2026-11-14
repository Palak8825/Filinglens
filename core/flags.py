"""
Deterministic red flags. No LLM here. Every flag carries the Fact rows (filing + page or XBRL URL)
it came from. Thresholds are deliberately simple and stated; README reports them as-is.

Inputs
  shareholding   ingest/shareholding.py  (NSE quarterly shareholding pattern, XBRL)
  auditor_event  ingest/auditor.py       (BSE announcements + audit reports in results filings)
  float          ingest/market.py       (Yahoo Finance prices + NSE public shareholding)
  rpt            producer not built (needs annual reports) — this flag stays silent until it is
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

from sqlalchemy import select

from core.db import Fact, get_session


@dataclass
class Flag:
    code: str
    severity: int  # 1..3
    title: str
    detail: str
    evidence: list[dict] = field(default_factory=list)


THRESHOLDS = {
    "encumbrance_creep_pp": 10.0,  # encumbered % of promoter holding up by ≥ this over 4 quarters
    "encumbrance_high_abs": 50.0,  # or encumbered level ≥ this (pledge + NDU + other, SEBI definition)
    "promoter_sell_qoq_pp": 3.0,   # promoter stake down ≥ this in one quarter (last 4 quarters)
    "promoter_sell_cum_pp": 5.0,   # or down ≥ this across the 12-quarter window
    "rpt_growth": 0.5,             # related-party txn / revenue up ≥ 50% YoY
    "float_pump_return": 1.0,      # 3m return ≥ 100%
    "float_pump_free_float": 25.0, # with free float < 25%
}


def _facts(symbol: str, kind: str) -> list[Fact]:
    with get_session() as s:
        return list(s.execute(select(Fact).where(Fact.symbol == symbol, Fact.kind == kind).order_by(Fact.as_of)).scalars())


_VALUE_KEYS = {
    "shareholding": ["promoter_pct", "encumbered_pct_of_promoter", "pledged_pct_of_promoter",
                     "ndu_pct_of_promoter", "other_encumbrance_pct_of_promoter", "public_pct"],
    "float": ["free_float_pct", "max_ret_3m", "ret_3m", "price_start", "price_end"],
}


def _ev(f: Fact) -> dict:
    p = f.payload or {}
    # Structured sources (NSE XBRL, prices) have no PDF page to show, so carry the numbers themselves.
    values = {k: p[k] for k in _VALUE_KEYS.get(f.kind, []) if p.get(k) is not None}
    return {"document_id": f.document_id, "page": f.page, "period": f.period,
            "as_of": str(f.as_of) if f.as_of else None, "source_url": p.get("source_url"),
            "path": p.get("path"), "quote": p.get("quote"), "title": p.get("title") or p.get("source"),
            "kind": f.kind, "values": values}


def _split(p: dict) -> str:
    if not p.get("encumbrance_split_available"):
        return ""
    return (f" (pledge {p.get('pledged_pct_of_promoter', 0):.1f}%, non-disposal undertakings "
            f"{p.get('ndu_pct_of_promoter', 0):.1f}%, other {p.get('other_encumbrance_pct_of_promoter', 0):.1f}%)")


def promoter_flags(symbol: str) -> list[Flag]:
    sh = [f for f in _facts(symbol, "shareholding") if f.payload.get("promoter_pct") is not None]
    out: list[Flag] = []
    if len(sh) < 2 or sh[-1].payload["promoter_pct"] <= 0:
        return out  # no promoter (e.g. YESBANK): encumbrance and selling flags don't apply
    latest = sh[-1]
    lp = latest.payload

    enc = [f for f in sh if f.payload.get("encumbered_pct_of_promoter") is not None]
    if enc and enc[-1] is latest:
        prior = enc[max(0, len(enc) - 5)]
        le, pe = lp["encumbered_pct_of_promoter"], prior.payload["encumbered_pct_of_promoter"]
        if le - pe >= THRESHOLDS["encumbrance_creep_pp"]:
            out.append(Flag("pledge_creep", 3, "Promoter encumbrance rising",
                            f"Encumbered share of promoter holding rose from {pe:.1f}% ({prior.period}) to "
                            f"{le:.1f}% ({latest.period}){_split(lp)}.", [_ev(prior), _ev(latest)]))
        elif le >= THRESHOLDS["encumbrance_high_abs"]:
            out.append(Flag("pledge_high", 2, "High promoter encumbrance",
                            f"{le:.1f}% of promoter holding is encumbered as of {latest.period}{_split(lp)}.", [_ev(latest)]))

    recent = sh[-5:]
    qoq = max(((a, b) for a, b in zip(recent, recent[1:])),
              key=lambda ab: ab[0].payload["promoter_pct"] - ab[1].payload["promoter_pct"], default=None)
    first = sh[0]
    cum = first.payload["promoter_pct"] - lp["promoter_pct"]
    if qoq and qoq[0].payload["promoter_pct"] - qoq[1].payload["promoter_pct"] >= THRESHOLDS["promoter_sell_qoq_pp"]:
        a, b = qoq
        out.append(Flag("promoter_stake_drop", 2, "Promoter stake fell sharply",
                        f"Promoter holding fell {a.payload['promoter_pct'] - b.payload['promoter_pct']:.2f}pp in one quarter, "
                        f"from {a.payload['promoter_pct']:.2f}% ({a.period}) to {b.payload['promoter_pct']:.2f}% ({b.period}).",
                        [_ev(a), _ev(b)]))
    elif cum >= THRESHOLDS["promoter_sell_cum_pp"]:
        out.append(Flag("promoter_stake_drop", 2, "Promoter stake declining",
                        f"Promoter holding fell {cum:.2f}pp, from {first.payload['promoter_pct']:.2f}% ({first.period}) "
                        f"to {lp['promoter_pct']:.2f}% ({latest.period}).", [_ev(first), _ev(latest)]))
    return out


AUDITOR_RULES = {
    "resignation": (3, "Statutory auditor resigned"),
    "qualified_opinion": (3, "Modified audit opinion"),
    "subsidiary_resignation": (2, "Subsidiary auditor resigned"),
}


def auditor_flags(symbol: str) -> list[Flag]:
    out: list[Flag] = []
    events = _facts(symbol, "auditor_event")
    for kind, (sev, title) in AUDITOR_RULES.items():
        evs = [f for f in events if f.payload.get("event") == kind]
        if not evs:
            continue
        if kind == "qualified_opinion":
            labels = sorted({f.payload.get("opinion", "qualified") for f in evs})
            dates = ", ".join(f.payload.get("filed_on", str(f.as_of)) for f in evs)
            detail = f"{' / '.join(labels).capitalize()} opinion in {len(evs)} audited result filing(s): {dates}."
        else:
            detail = " ".join(f.payload.get("summary", "") for f in evs[-3:])
        out.append(Flag("auditor_change" if kind != "qualified_opinion" else "audit_qualification", sev, title, detail,
                        [_ev(f) for f in evs]))
    return out


def rpt_flags(symbol: str) -> list[Flag]:
    r = _facts(symbol, "rpt")
    if len(r) >= 2:
        a, b = r[-2].payload.get("rpt_to_revenue", 0.0), r[-1].payload.get("rpt_to_revenue", 0.0)
        if a > 0 and (b - a) / a >= THRESHOLDS["rpt_growth"]:
            return [Flag("rpt_spike", 2, "Related-party transactions rising",
                         f"RPT/revenue rose from {a:.1%} to {b:.1%}.", [_ev(r[-2]), _ev(r[-1])])]
    return []


def float_flags(symbol: str) -> list[Flag]:
    f = _facts(symbol, "float")
    if f:
        p = f[-1].payload
        ret = p.get("max_ret_3m", p.get("ret_3m", 0))
        if ret >= THRESHOLDS["float_pump_return"] and p.get("free_float_pct", 100) < THRESHOLDS["float_pump_free_float"]:
            w = p.get("max_window") or ["?", "?"]
            return [Flag("float_pump", 3, "Low float, extreme move",
                         f"Price rose {ret:.0%} in 3 months ({w[0]} to {w[1]}, Rs {p.get('price_start')} to "
                         f"Rs {p.get('price_end')}) with only {p['free_float_pct']:.1f}% public float "
                         f"({p.get('free_float_period')}).", [_ev(f[-1])])]
    return []


def score(symbol: str) -> dict:
    flags = promoter_flags(symbol) + auditor_flags(symbol) + rpt_flags(symbol) + float_flags(symbol)
    total = sum(f.severity for f in flags)
    level = "high" if total >= 5 else "elevated" if total >= 2 else "low"
    return {"symbol": symbol, "score": total, "level": level, "flags": [asdict(f) for f in flags], "thresholds": THRESHOLDS}


# Back-compat for callers of the old name.
pledge_flags = promoter_flags
