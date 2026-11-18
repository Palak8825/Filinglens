"""
Unpack the FilingLens release assets into ./data (run from the repo root).

    python -m scripts.setup_release path/to/downloaded/assets          # full release (index + all PDFs)
    python -m scripts.setup_release path/to/downloaded/assets --demo   # small demo asset only

Verifies every zip against SHA256SUMS.txt before extracting. Stdlib only, works on Windows/macOS/Linux.
"""
from __future__ import annotations

import hashlib
import sys
import zipfile
from pathlib import Path


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def main(folder: Path, demo: bool = False) -> int:
    sums_file = folder / ("SHA256SUMS-demo.txt" if demo else "SHA256SUMS.txt")
    if not sums_file.exists():
        print(f"missing {sums_file}")
        return 1
    expected = dict(reversed(line.split(None, 1)) for line in sums_file.read_text().splitlines() if line.strip())
    root = Path.cwd()
    for name, digest in sorted(expected.items()):
        z = folder / name.strip()
        if not z.exists():
            print(f"missing {z} — download every asset from the release page")
            return 1
        print(f"checking {z.name} ...", end=" ", flush=True)
        if sha256(z) != digest:
            print("CHECKSUM MISMATCH — re-download this file")
            return 1
        with zipfile.ZipFile(z) as zf:
            for member in zf.namelist():
                target = (root / member).resolve()
                if root.resolve() not in target.parents or not member.startswith("data/"):
                    print(f"refusing unexpected path {member}")
                    return 1
            zf.extractall(root)
        print("ok, extracted")
    print("Done. Next: run `uvicorn api.main:app`, then `streamlit run web/app.py` in a second terminal.\n"
          "No API key needed for the example answers, briefs, red flags and dashboard; "
          "optionally set LLM_API_KEY in .env (copy .env.example) to ask your own questions.")
    return 0


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--demo"]
    if len(args) != 1:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(Path(args[0]), demo="--demo" in sys.argv[1:]))
