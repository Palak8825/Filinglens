"""
Deterministic red flags. No LLM here. Every flag carries the Fact row (document + page) it came from.
Thresholds are deliberately simple and stated; tune after looking at the data, and say so in README.
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
    evidence: list[dict] = field(default_factory=list)  # [{document_id, page, period}]


THRESHOLDS = {
    "pledge_creep_pp": 10.0,       # pledged % of promoter holding up by ≥ this over 4 quarters
    "pledge_high_abs": 50.0,       # or absolute level ≥ this
    "promoter_sell_pp": 3.0,       # promoter stake down ≥ this in one quarter
    "guidance_miss_count": 2,      # misses in last 4 quarters
    "rpt_growth": 0.5,             # related-party txn / revenue up ≥ 50% YoY
    "float_pump_return": 1.0,      # 3m return ≥ 100%
    "float_pump_free_float": 25.0, # with free float < 25%
}


def _facts(symbol: str, kind: str) -> list[Fact]:
    with get_session() as s:
        return list(s.execute(select(Fact).where(Fact.symbol == symbol, Fact.kind == kind).order_by(Fact.as_of)).scalars())


def _ev(f: Fact) -> dict:
    return {"document_id": f.document_id, "page": f.page, "period": f.period}


def pledge_flags(symbol: str) -> list[Flag]:
    sh = _facts(symbol, "shareholding")
    out: list[Flag] = []
    if len(sh) >= 2:
        latest, prior = sh[-1], sh[max(0, len(sh) - 5)]
        lp, pp = latest.payload.get("pledged_pct_of_promoter", 0.0), prior.payload.get("pledged_pct_of_promoter", 0.0)
        if lp - pp >= THRESHOLDS["pledge_creep_pp"]:
            out.append(Flag("pledge_creep", 3, "Promoter pledge rising",
                            f"Pledged share of promoter holding rose from {pp:.1f}% to {lp:.1f}%.", [_ev(prior), _ev(latest)]))
        elif lp >= THRESHOLDS["pledge_high_abs"]:
            out.append(Flag("pledge_high", 2, "High promoter pledge", f"{lp:.1f}% of promoter holding is pledged.", [_ev(latest)]))
        d = prior.payload.get("promoter_pct", 0.0) - latest.payload.get("promoter_pct", 0.0)
        if len(sh) >= 2 and (sh[-2].payload.get("promoter_pct", 0) - latest.payload.get("promoter_pct", 0)) >= THRESHOLDS["promoter_sell_pp"]:
            out.append(Flag("promoter_selling", 2, "Promoter stake fell",
                            f"Promoter holding down {d:.1f}pp in one quarter.", [_ev(sh[-2]), _ev(latest)]))
    return out


def auditor_flags(symbol: str) -> list[Flag]:
    ev = _facts(symbol, "auditor_event")
    return [Flag("auditor_change", 3, "Auditor change / resignation", f.payload.get("summary", ""), [_ev(f)]) for f in ev
            if f.payload.get("event") in {"resignation", "non_reappointment", "qualified_opinion"}]


def guidance_flags(symbol: str) -> list[Flag]:
    g = _facts(symbol, "guidance")[-4:]
    misses = [f for f in g if f.payload.get("outcome") == "miss"]
    if len(misses) >= THRESHOLDS["guidance_miss_count"]:
        return [Flag("guidance_miss", 2, "Repeated guidance misses",
                     f"{len(misses)} of last {len(g)} guided metrics were missed.", [_ev(f) for f in misses])]
    return []


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
        if p.get("ret_3m", 0) >= THRESHOLDS["float_pump_return"] and p.get("free_float_pct", 100) < THRESHOLDS["float_pump_free_float"]:
            return [Flag("float_pump", 3, "Low float, extreme move",
                         f"3-month return {p['ret_3m']:.0%} on {p['free_float_pct']:.0f}% free float.", [_ev(f[-1])])]
    return []


def score(symbol: str) -> dict:
    flags = pledge_flags(symbol) + auditor_flags(symbol) + guidance_flags(symbol) + rpt_flags(symbol) + float_flags(symbol)
    total = sum(f.severity for f in flags)
    level = "high" if total >= 5 else "elevated" if total >= 2 else "low"
    return {"symbol": symbol, "score": total, "level": level, "flags": [asdict(f) for f in flags], "thresholds": THRESHOLDS}
