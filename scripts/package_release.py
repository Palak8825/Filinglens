"""
Package the prebuilt index + the PDFs the demo can cite into GitHub-release-sized zips, so a judge
can run FilingLens without re-downloading from BSE or re-embedding (~2 h on CPU).

    python -m scripts.package_release --out ../filinglens-release          # full release
    python -m scripts.package_release --out ../filinglens-release --demo   # small demo asset only

Produces
  filinglens-index.zip        SQLite index, vector files, manifest, cached shareholding XBRL + prices
  filinglens-pdfs-partN.zip   every PDF in the index or cited by a flag (each part < 1.9 GB)
  SHA256SUMS.txt
or, with --demo,
  filinglens-demo.zip         index for the symbols in demo/answer_cache.json, every PDF the cached answers
                              cite, those symbols' latest two quarters of results + concalls, and the
                              auditor-flag evidence. Flags still cover all symbols (facts are kept whole).
  SHA256SUMS-demo.txt
Unpack with `python -m scripts.setup_release <folder>` (or `... <folder> --demo`) from the repo root.
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
DEMO_ZIP = "filinglens-demo.zip"


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
    produced: list[Path] = []  # SHA256SUMS lists only what THIS run wrote (see the end of main)
    stale = [p for p in sorted(out.glob("filinglens-*.zip")) if p.name != DEMO_ZIP]
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
    produced.append(idx_zip)
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
            produced.append(out / f"filinglens-pdfs-part{part}.zip")
            z = zipfile.ZipFile(produced[-1], "w", zipfile.ZIP_STORED)
        z.write(f, _arc(f))
        size += n
    if z:
        z.close()
    print(f"PDFs: {len(files)} files in {part} part(s)")

    write_sums(out, produced, stale)


def write_sums(out: Path, produced: list[Path], stale: list[Path]) -> None:
    """Checksum only the files this build wrote. Globbing the folder would list a leftover zip from an
    older build (e.g. a pdfs-part3 when this build needs two parts) as a required download, and the
    judge's setup_release run would fail on it. Leftovers are reported, never deleted."""
    sums = [f"{_sha256(p)}  {p.name}" for p in produced]
    (out / "SHA256SUMS.txt").write_text("\n".join(sums) + "\n", encoding="utf-8")
    for p in produced:
        print(f"  {p.name:<28} {p.stat().st_size / 1e6:8.0f} MB")
    leftovers = [p for p in stale if p not in produced]
    if leftovers:
        print("NOTE: not part of this build, not in SHA256SUMS.txt; left in place, do not upload:")
        for p in leftovers:
            print(f"  {p.name}")


def build_demo(out: Path) -> None:
    """Small asset for judges: enough to run the cached demo questions with page images and no API key.
    Built from the answer cache, so every page a cached answer cites is in the zip."""
    import json
    import tempfile

    import numpy as np

    from core import answer_cache, vectors

    out.mkdir(parents=True, exist_ok=True)
    entries = json.loads(answer_cache.CACHE_FILE.read_text(encoding="utf-8"))["entries"].values()
    if not entries:
        raise SystemExit("demo/answer_cache.json is empty; run `python -m scripts.warm_cache` first.")
    symbols = sorted({e["symbol"] for e in entries})
    cited_ids = {c["document_id"] for e in entries for c in e["answer"]["citations"]}

    db_file = Path(settings.database_url.split("///", 1)[1])
    con = sqlite3.connect(db_file)
    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    con.close()

    with get_session() as s:
        docs = s.execute(select(Document).where(Document.symbol.in_(symbols))).scalars().all()
        auditor = {f.payload["path"] for f in s.execute(select(Fact).where(Fact.kind == "auditor_event")).scalars()
                   if f.payload.get("path")}
    pdfs = {d.path for d in docs if d.id in cited_ids}
    for sym in symbols:  # latest two filed quarters of results + concalls: where live questions mostly land
        qs = sorted({d.fiscal_period for d in docs if d.symbol == sym and d.doc_type in ("results", "concall")
                     and d.fiscal_period and d.fiscal_period.startswith("Q")},
                    key=lambda p: (p[4:], p[:2]), reverse=True)[:2]
        pdfs |= {d.path for d in docs if d.symbol == sym and d.doc_type in ("results", "concall") and d.fiscal_period in qs}
    pdfs |= auditor
    missing_cited = [d.path for d in docs if d.id in cited_ids and not Path(d.path.replace("\\", "/")).exists()]
    if missing_cited:
        raise SystemExit(f"cited PDFs missing on disk: {missing_cited[:3]}")

    with tempfile.TemporaryDirectory() as tmp:
        sub_db = Path(tmp) / "filinglens.db"
        src, dst = sqlite3.connect(db_file), sqlite3.connect(sub_db)
        src.backup(dst)
        src.close()
        ph = ",".join("?" * len(symbols))
        dst.execute(f"DELETE FROM chunks WHERE document_id IN (SELECT id FROM documents WHERE symbol NOT IN ({ph}))", symbols)
        dst.execute(f"DELETE FROM documents WHERE symbol NOT IN ({ph})", symbols)
        dst.execute("UPDATE facts SET document_id = NULL WHERE document_id IS NOT NULL "
                    "AND document_id NOT IN (SELECT id FROM documents)")
        dst.commit()
        keep = {r[0] for r in dst.execute("SELECT id FROM chunks")}
        dst.execute("VACUUM")
        dst.close()

        v, ids = vectors.load()
        mask = np.isin(ids, np.fromiter(keep, dtype=np.int64, count=len(keep)))
        vec_f, ids_f = Path(tmp) / "vectors.f32", Path(tmp) / "chunk_ids.i64"
        np.ascontiguousarray(v[mask], dtype=np.float32).tofile(vec_f)
        np.ascontiguousarray(ids[mask], dtype=np.int64).tofile(ids_f)

        zpath = out / DEMO_ZIP
        files = sorted(Path(p.replace("\\", "/")) for p in pdfs)
        files = [f for f in files if f.exists()]
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            z.write(sub_db, "data/filinglens.db")
            z.write(vec_f, "data/processed/vectors.f32")
            z.write(ids_f, "data/processed/chunk_ids.i64")
            manifest = settings.data_dir / "raw" / "manifest.jsonl"
            if manifest.exists():  # the corpus listing: the landing/home "filings collected" count comes from it
                z.write(manifest, "data/raw/manifest.jsonl")
            for f in files:
                z.write(f, _arc(f), compress_type=zipfile.ZIP_STORED)
    (out / "SHA256SUMS-demo.txt").write_text(f"{_sha256(zpath)}  {zpath.name}\n", encoding="utf-8")
    print(f"{DEMO_ZIP}: {', '.join(symbols)} · {len(keep):,} chunks · {len(files)} PDFs "
          f"({len(cited_ids)} cited docs) · {zpath.stat().st_size / 1e6:.0f} MB")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="../filinglens-release")
    ap.add_argument("--demo", action="store_true", help="build only the small demo asset")
    a = ap.parse_args()
    build_demo(Path(a.out)) if a.demo else main(Path(a.out))
