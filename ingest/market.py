"""
Price data → Fact(kind="float") rows for the float_pump flag.

  free_float_pct  public shareholding % from the latest NSE shareholding pattern (ingest/shareholding.py)
  max_ret_3m      largest rolling 63-trading-day return in the last 12 months (catches a pump that
                  has since faded), with the start/end dates of that window
  ret_3m          the latest 63-day return

Daily closes come from Yahoo Finance via yfinance and are cached in data/raw/<SYMBOL>/prices.csv,
so re-runs are offline and the numbers stay frozen with the rest of the corpus.

    python -m ingest.market --symbols GENSOL,VEDL
    python -m ingest.market --offline
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path

import pandas as pd
from sqlalchemy import select

from core.config import settings
from core.db import Fact, get_session, init_db

UNIVERSE = Path(__file__).resolve().parent.parent / "scripts" / "universe.csv"
WINDOW = 63  # ~3 months of trading days


def _prices(symbol: str, offline: bool) -> pd.Series | None:
    path = settings.data_dir / "raw" / symbol / "prices.csv"
    if not path.exists() and not offline:
        import yfinance as yf

        df = yf.Ticker(f"{symbol}.NS").history(period="15mo", auto_adjust=True)
        if df is None or df.empty:
            return None
        path.parent.mkdir(parents=True, exist_ok=True)
        df[["Close"]].rename_axis("date").to_csv(path)
    if not path.exists():
        return None
    s = pd.read_csv(path, index_col="date")["Close"].dropna()
    s.index = pd.to_datetime(s.index, utc=True).tz_convert(None).normalize()
    return s.sort_index()


def build_fact(symbol: str, offline: bool = False) -> Fact | None:
    with get_session() as s:
        sh = s.execute(select(Fact).where(Fact.symbol == symbol, Fact.kind == "shareholding")
                       .order_by(Fact.as_of.desc())).scalars().first()
    if sh is None or sh.payload.get("public_pct") is None:
        return None
    px = _prices(symbol, offline)
    if px is None or len(px) <= WINDOW:
        return None
    last_year = px[px.index >= px.index[-1] - pd.Timedelta(days=365)]
    rets = (px / px.shift(WINDOW) - 1).loc[last_year.index].dropna()
    if rets.empty:
        return None
    end = rets.idxmax()
    start = px.index[px.index.get_loc(end) - WINDOW]
    payload = {
        "free_float_pct": float(sh.payload["public_pct"]),
        "free_float_period": sh.period,
        "ret_3m": round(float(rets.iloc[-1]), 4),
        "max_ret_3m": round(float(rets.max()), 4),
        "max_window": [str(start.date()), str(end.date())],
        "price_start": round(float(px.loc[start]), 2),
        "price_end": round(float(px.loc[end]), 2),
        "prices_through": str(px.index[-1].date()),
        "source": "Yahoo Finance daily closes (adjusted) + NSE shareholding pattern",
        "source_url": sh.payload.get("source_url"),
    }
    return Fact(symbol=symbol, kind="float", period=sh.period, as_of=px.index[-1].date(), payload=payload)


def ingest_market(symbols: list[str], offline: bool = False) -> None:
    init_db()
    for sym in symbols:
        try:
            fact = build_fact(sym, offline)
        except Exception as e:  # noqa: BLE001
            print(f"  ERR {sym}: {type(e).__name__}: {e}")
            continue
        with get_session() as s:
            s.query(Fact).filter(Fact.symbol == sym, Fact.kind == "float").delete()
            if fact:
                s.add(fact)
            s.commit()
        if fact:
            p = fact.payload
            print(f"== {sym}: free float {p['free_float_pct']:.1f}% | 3m {p['ret_3m']:+.0%} | "
                  f"max 3m {p['max_ret_3m']:+.0%} ({p['max_window'][0]}..{p['max_window'][1]})")
        else:
            print(f"== {sym}: no data")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--symbols", help="comma-separated; default: whole universe")
    p.add_argument("--offline", action="store_true")
    a = p.parse_args()
    if a.symbols:
        syms = [x.strip().upper() for x in a.symbols.split(",")]
    else:
        with UNIVERSE.open(encoding="utf-8") as f:
            syms = [r["symbol"] for r in csv.DictReader(f)]
    ingest_market(syms, a.offline)
