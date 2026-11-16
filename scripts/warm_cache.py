"""
Generate cached answers for the demo questions in demo/questions.json → demo/answer_cache.json.

    python -m scripts.warm_cache              # answer every question not yet cached (live, needs LLM_API_KEY)
    python -m scripts.warm_cache --refresh    # re-answer all of them (after a prompt / retrieval / index change)
    python -m scripts.warm_cache --list       # show what's cached and which documents the answers cite
    python -m scripts.warm_cache --redo "IDEA|bank debt"   # re-answer questions matching symbol|substring

The cache mirrors demo/questions.json: entries for questions no longer listed there are removed (and
printed), so an edited question never leaves a stale example button behind.

Each answer goes through the normal pipeline (retrieve → rerank → LLM → citation verifier) and is stored
whole: answer text, verified citations, dropped-citation count, the model that actually answered.
Rebuild the demo release asset afterwards (`python -m scripts.package_release --demo`), since it ships
exactly the PDFs these answers cite.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from core import answer_cache
from core.answer import ask
from core.llm import has_api_key

QUESTIONS = Path(__file__).resolve().parent.parent / "demo" / "questions.json"


def load_questions() -> list[dict]:
    return json.loads(QUESTIONS.read_text(encoding="utf-8"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--redo", action="append", default=[], help="SYMBOL|substring of a question to re-answer")
    ap.add_argument("--pace", type=float, default=3.0, help="seconds between questions (free-tier rate limits)")
    a = ap.parse_args()
    prompt = os.environ.get("PROMPT_VERSION", "v1")

    if a.list:
        for q in load_questions():
            hit = answer_cache.get(q["symbol"], q["question"], prompt, has_key=False)
            if not hit:
                print(f"  MISSING  {q['symbol']:<10} {q['question']}")
                continue
            docs = sorted({c["title"][:60] + f" p{c['page']}" for c in hit["citations"]})
            print(f"  cached   {q['symbol']:<10} {q['question']}\n           {len(hit['citations'])} verified, "
                  f"{hit['dropped_citations']} dropped, {hit['cached']['model']} · " + "; ".join(docs))
        return

    if not has_api_key():
        raise SystemExit("LLM_API_KEY is not set; warming the cache needs live answers.")
    questions = load_questions()
    for gone in answer_cache.prune({(q["symbol"], q["question"]) for q in questions}, prompt):
        print(f"  removed stale entry: {gone}")
    redo = [r.split("|", 1) for r in a.redo]
    for q in questions:
        forced = any(q["symbol"] == sym.upper() and sub.lower() in q["question"].lower() for sym, sub in redo)
        if not (a.refresh or forced) and answer_cache.get(q["symbol"], q["question"], prompt, has_key=True):
            continue
        t = time.time()
        ans = ask(q["symbol"], q["question"]).to_dict()
        answer_cache.put(q["symbol"], q["question"], prompt, ans)
        print(f"  {q['symbol']:<10} {time.time() - t:4.1f}s  {len(ans['citations'])} verified / "
              f"{ans['dropped_citations']} dropped  {ans['model']}  {q['question'][:70]}")
        time.sleep(a.pace)
    print(f"{answer_cache.size()} cached answers in {answer_cache.CACHE_FILE}")


if __name__ == "__main__":
    main()
