"""
Curated answer cache for the demo questions, committed to the repo (demo/answer_cache.json).

Why it exists: a judge who clones the repo without a Gemini key still gets full answers to the demo
questions (citations, verifier results, page images), and a recording session survives a free-tier
503. It is disclosed: every cached answer carries a `cached` block that the UI shows as a badge.

Key: (symbol, normalised question, prompt version), plus the requested model when an API key is set.
Without a key the model is ignored: a judge with no key isn't choosing a model, and a slightly
different .env must not turn a hit into a dead box. The index identity (that symbol's chunk count, the
same in the full and the demo download) is stored as metadata and compared at read time; a mismatch is shown, never a silent miss (mtime would change on
every unzip, so it is deliberately not used).

Only `python -m scripts.warm_cache` writes entries; live questions are never added automatically.
"""
from __future__ import annotations

import json
import re
import threading
from datetime import date
from functools import lru_cache
from pathlib import Path

from sqlalchemy import func, select

from core.config import settings

CACHE_FILE = Path(__file__).resolve().parent.parent / "demo" / "answer_cache.json"
_lock = threading.Lock()


def norm_question(q: str) -> str:
    q = re.sub(r"\s+", " ", q.strip().lower())
    return q.rstrip(" ?.!")


def _key(symbol: str, question: str, prompt: str) -> str:
    return f"{symbol.upper()}|{prompt}|{norm_question(question)}"


def _read() -> dict:
    if not CACHE_FILE.exists():
        return {"entries": {}}
    return json.loads(CACHE_FILE.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _loaded(stamp: tuple[int, int]) -> dict:  # re-read when the file changes
    return _read()


def _data() -> dict:
    if not CACHE_FILE.exists():
        return {"entries": {}}
    st = CACHE_FILE.stat()
    return _loaded((st.st_mtime_ns, st.st_size))


@lru_cache(maxsize=64)
def index_chunks(symbol: str) -> int:
    from core.db import Chunk, Document, get_session

    with get_session() as s:
        return int(s.execute(select(func.count(Chunk.id)).join(Document)
                             .where(Document.symbol == symbol.upper())).scalar() or 0)


def get(symbol: str, question: str, prompt: str, has_key: bool) -> dict | None:
    """The cached Answer dict (with a `cached` metadata block), or None."""
    e = _data()["entries"].get(_key(symbol, question, prompt))
    if not e:
        return None
    if has_key and e["requested_model"] != settings.llm_model:
        return None  # with a key, a different configured model means answer live
    ans = dict(e["answer"])
    ans["cached"] = {"generated_on": e["generated_on"], "model": e["answer"].get("model") or e["requested_model"],
                     "index_chunks": e["index_chunks"], "index_changed": e["index_chunks"] != index_chunks(symbol)}
    return ans


def put(symbol: str, question: str, prompt: str, answer: dict) -> None:
    with _lock:
        data = _read()
        answer = {k: v for k, v in answer.items() if k != "cached"}
        data["entries"][_key(symbol, question, prompt)] = {
            "symbol": symbol.upper(), "question": question.strip(), "prompt": prompt,
            "requested_model": settings.llm_model, "generated_on": date.today().isoformat(),
            "index_chunks": index_chunks(symbol), "answer": answer,
        }
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = CACHE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(CACHE_FILE)


def prune(keep: set[tuple[str, str]], prompt: str) -> list[str]:
    """Drop entries for this prompt version whose (symbol, question) is not in `keep`. Returns their keys."""
    wanted = {_key(sym, q, prompt) for sym, q in keep}
    with _lock:
        data = _read()
        gone = [k for k, e in data["entries"].items() if e["prompt"] == prompt and k not in wanted]
        if gone:
            for k in gone:
                del data["entries"][k]
            tmp = CACHE_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(CACHE_FILE)
    return gone


def examples(symbol: str | None = None) -> list[dict]:
    """Cached questions, for the clickable examples in the UI."""
    out = [{"symbol": e["symbol"], "question": e["question"]} for e in _data()["entries"].values()]
    return [x for x in out if symbol is None or x["symbol"] == symbol.upper()]


def size() -> int:
    return len(_data()["entries"])
