"""
Fetch the frozen corpus for the universe in scripts/universe.csv.
Run once on day 1, commit nothing from data/raw (gitignored), and host a zip of data/raw on
GitHub Releases / Drive so judges can reproduce without hitting BSE.
"""
from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

from ingest.announcements import append_manifest, download, fetch_bse_announcements

UNIVERSE = Path(__file__).parent / "universe.csv"


def main(start: str = "2023-04-01") -> None:
    with UNIVERSE.open() as f:
        for row in csv.DictReader(f):
            print("==", row["symbol"])
            for ann in fetch_bse_announcements(row["bse_code"], row["symbol"], date.fromisoformat(start), date.today()):
                try:
                    append_manifest(download(ann))
                except Exception as e:  # noqa: BLE001
                    print("  ERR", ann.title[:60], e)


if __name__ == "__main__":
    main()
