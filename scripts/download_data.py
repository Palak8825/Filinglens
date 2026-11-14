"""
Fetch the frozen corpus for the universe in scripts/universe.csv.
Run once on day 1, commit nothing from data/raw (gitignored), and host a zip of data/raw on
GitHub Releases / Drive so judges can reproduce without hitting BSE.

Resumable: anything already in data/raw/manifest.jsonl is skipped, so just re-run after a crash.
    python -m scripts.download_data                    # whole universe
    python -m scripts.download_data --only ZEEL YESBANK
"""
from __future__ import annotations

import argparse
import csv
import time
from collections import Counter
from datetime import date
from pathlib import Path

from ingest.announcements import _candidate_urls, append_manifest, download, fetch_bse_announcements, read_manifest

UNIVERSE = Path(__file__).parent / "universe.csv"


def main(start: str = "2023-04-01", only: list[str] | None = None, delay: float = 0.5) -> None:
    seen = {u for a in read_manifest() for u in _candidate_urls(a.source_url)}
    with UNIVERSE.open(encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if not only or r["symbol"] in only]
    for row in rows:
        sym = row["symbol"]
        stats: Counter[str] = Counter()
        try:
            for ann in fetch_bse_announcements(row["bse_code"], sym, date.fromisoformat(start), date.today()):
                if ann.source_url in seen:
                    stats["skip"] += 1
                    continue
                try:
                    append_manifest(download(ann))
                    seen.update(_candidate_urls(ann.source_url))
                    stats[ann.doc_type] += 1
                    time.sleep(delay)
                except Exception as e:  # noqa: BLE001
                    stats["err"] += 1
                    print(f"  ERR {sym} {ann.filed_on} {ann.title[:60]}: {e}")
        except Exception as e:  # noqa: BLE001 - listing failed; move on, re-run later
            print(f"  LIST-ERR {sym}: {e}")
        print(f"== {sym}: {dict(stats)}", flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="2023-04-01")
    p.add_argument("--only", nargs="*")
    p.add_argument("--delay", type=float, default=0.5)
    a = p.parse_args()
    main(a.start, a.only, a.delay)
