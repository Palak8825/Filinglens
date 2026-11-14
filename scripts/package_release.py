"""
Package the prebuilt index + the PDFs the demo can cite into GitHub-release-sized zips, so a judge
can run FilingLens without re-downloading from BSE or re-embedding (~2 h on CPU).

    python -m scripts.package_release --out ../filinglens-release

Produces
  filinglens-index.zip        SQLite index, vector files, manifest, cached shareholding XBRL + prices
  filinglens-pdfs-partN.zip   every PDF in the index or cited by a flag (each part < 1.9 GB)
  SHA256SUMS.txt
Unpack with `python -m scripts.setup_release <folder>` from the repo root.
"""
from __future__ import annotations

import argparse
import hashlib
import sqlite3
import zipfile
from pathlib import Path

from sqlalchemy import select

from core.config import settings
from core.db import Document, Fact, get_session

PART_LIMIT = int(1.9e9)  # GitHub caps each release asset at 2 GB


def _arc(p: Path) -> str:
    return p.as_posix().lstrip("./")


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def main(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    data = settings.data_dir
    db_file = Path(settings.database_url.split("///", 1)[1])

    # Fold the WAL into the main file so the shipped .db is complete on its own.
    con = sqlite3.connect(db_file)
    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    con.close()

    index_files = [db_file, data / "processed" / "vectors.f32", data / "processed" / "chunk_ids.i64",
                   data / "raw" / "manifest.jsonl"]
    index_files += sorted((data / "raw").glob("*/shareholding_xbrl/*"))
    index_files += sorted((data / "raw").glob("*/prices.csv"))
    idx_zip = out / "filinglens-index.zip"
    with zipfile.ZipFile(idx_zip, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for f in index_files:
            if f.exists():
                z.write(f, _arc(f))
    print(f"{idx_zip.name}: {len(index_files)} files, {idx_zip.stat().st_size / 1e6:.0f} MB")

    with get_session() as s:
        pdfs = {p for (p,) in s.execute(select(Document.path)).all()}
        pdfs |= {f.payload["path"] for f in s.execute(select(Fact).where(Fact.kind == "auditor_event")).scalars()
                 if f.payload.get("path")}
    files = sorted(Path(p.replace("\\", "/")) for p in pdfs)
    files = [f for f in files if f.exists()]
    part, size, z = 0, 0, None
    for f in files:
        n = f.stat().st_size
        if z is None or size + n > PART_LIMIT:
            if z:
                z.close()
            part += 1
            size = 0
            z = zipfile.ZipFile(out / f"filinglens-pdfs-part{part}.zip", "w", zipfile.ZIP_STORED)
        z.write(f, _arc(f))
        size += n
    if z:
        z.close()
    print(f"PDFs: {len(files)} files in {part} part(s)")

    sums = [f"{_sha256(p)}  {p.name}" for p in sorted(out.glob("filinglens-*.zip"))]
    (out / "SHA256SUMS.txt").write_text("\n".join(sums) + "\n", encoding="utf-8")
    for p in sorted(out.glob("filinglens-*.zip")):
        print(f"  {p.name:<28} {p.stat().st_size / 1e6:8.0f} MB")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="../filinglens-release")
    main(Path(ap.parse_args().out))
