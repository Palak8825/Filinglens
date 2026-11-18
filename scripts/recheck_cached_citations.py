"""
Re-verify every citation stored in the demo caches against the CURRENT verifier.

The prepared answers (demo/answer_cache.json) and Briefs (demo/brief_cache.json) are replayed, not
re-checked, so tightening the verifier could leave the repo shipping a citation the live code would now
reject, and nothing in the app would show it. Run this after any change to core/verify.py:

    python -m scripts.recheck_cached_citations

Exit code 0 = every cached citation still verifies; 1 = re-warm the listed entries
(scripts/warm_cache.py, scripts/warm_brief.py). Citations whose page isn't in the local index are skipped.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from sqlalchemy import select

from core.db import Chunk, get_session
from core.verify import quote_in_chunk

DEMO = Path(__file__).resolve().parent.parent / "demo"


def _chunks(document_id: int, page: int) -> list[str]:
    with get_session() as s:
        return [t for (t,) in s.execute(select(Chunk.text).where(Chunk.document_id == document_id,
                                                                  Chunk.page == page)).all()]


def _citations(obj):
    if isinstance(obj, dict):
        if "quote" in obj and "document_id" in obj:
            yield obj
        for v in obj.values():
            yield from _citations(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _citations(v)


def main() -> int:
    checked = failed = skipped = 0
    for name in ("answer_cache.json", "brief_cache.json"):
        entries = json.loads((DEMO / name).read_text(encoding="utf-8"))["entries"]
        for key, entry in entries.items():
            for c in _citations(entry):
                chunks = _chunks(c["document_id"], c["page"])
                if not chunks:
                    skipped += 1
                    continue
                checked += 1
                if not any(quote_in_chunk(c["quote"], t) for t in chunks):
                    failed += 1
                    print(f"[REJECTED NOW] {name} · {key} · doc {c['document_id']} p{c['page']}: {c['quote'][:140]}")
    print(f"checked {checked} cached citation(s) · {failed} would now be rejected · {skipped} skipped (page not indexed)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
