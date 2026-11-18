"""
Generate prepared Briefs → demo/brief_cache.json (live LLM; up to 4 calls per stock).

    python -m scripts.warm_brief --symbols YESBANK,IDEA,VEDL,RELIANCE,PAYTM
    python -m scripts.warm_brief --symbols IDEA --refresh     # regenerate even if cached
    python -m scripts.warm_brief --list                       # what's cached, covered vs not covered

Each section goes through the normal pipeline (retrieve → rerank → LLM → citation verifier) and is stored
whole. Red flags and notable events are deterministic and recomputed on every request, so they are not
stored. Rebuild the demo release asset afterwards (`python -m scripts.package_release --demo`): it ships
every page a cached brief cites.
"""
from __future__ import annotations

import argparse
import time

from core import brief
from core.llm import has_api_key

DEMO = "YESBANK,IDEA,VEDL,RELIANCE,PAYTM"


def summary(symbol: str) -> str:
    b = brief.get_brief(symbol)
    parts = []
    for s in b["sections"]:
        if "question" in s:
            parts.append(f"{s['key']}={'covered' if s['covered'] else 'not covered'}"
                         + (f" ({len(s['citations'])} cit)" if s["covered"] else ""))
    nb = next(s for s in b["sections"] if s["key"] == "notable")["notable"]
    n_sent = sum(1 for i in nb["items"] if i.get("sentence") and i["sentence"]["covered"])
    parts.append(f"notable={len(nb['items'])} items, {n_sent} cited sentence(s)")
    return ", ".join(parts)


RETRY_NOTE = " Quote the table row or sentence for every figure you state, and leave out any figure you cannot quote."


def score(ans: dict | None) -> tuple[int, int, int]:
    """Higher is better: shown at all, fewer sentences dropped by the number-grounding rule, more text shown."""
    r = brief.render_section(ans)
    if not r["covered"]:
        return (0, 0, 0)
    full = len(brief._SENTENCE.split(ans["answer"].strip()))
    shown = len(brief._SENTENCE.split(r["text"]))
    return (1, -(full - shown), len(r["text"]))


def improve(sym: str, llm: dict, only: set[str] | None, tries: int = 2) -> None:
    """A section that came back not covered, or had sentences dropped for unquoted figures, is asked again with a
    reminder to quote every figure; the best-scoring answer is kept (shown under the original question)."""
    for key, q in brief.questions(sym).items():
        if only is not None and key not in only:
            continue
        best = llm["sections"].get(key)
        for _ in range(tries):
            s = score(best)
            if s[0] and s[1] == 0:
                break
            alt = brief._ask(sym, q["question"] + RETRY_NOTE, q["doc_type"])
            alt["question"] = q["question"]
            if score(alt) > s:
                best = alt
        llm["sections"][key] = best


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default=DEMO)
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--pace", type=float, default=2.0)
    ap.add_argument("--sections", default="", help="rebuild only these parts of a cached brief: quarter,management,notable")
    a = ap.parse_args()
    if a.list:
        for sym in brief.cached_symbols():
            print(f"  {sym:<10} {brief.cache_get(sym)['generated_on']}  {summary(sym)}")
        return
    if not has_api_key():
        raise SystemExit("LLM_API_KEY is not set; preparing a brief needs live answers.")
    for sym in [s.strip().upper() for s in a.symbols.split(",") if s.strip()]:
        only = {x.strip() for x in a.sections.split(",") if x.strip()} or None
        cached = brief.cache_get(sym)
        if cached and not (a.refresh or only):
            print(f"  {sym:<10} cached already (use --refresh or --sections)")
            continue
        t = time.time()
        llm = brief.build_llm_parts(sym, only, cached["llm"] if (cached and only) else None)
        improve(sym, llm, only)
        brief.cache_put(sym, llm)
        print(f"  {sym:<10} {time.time() - t:5.1f}s  {summary(sym)}")
        time.sleep(a.pace)
    print(f"{len(brief.cached_symbols())} prepared briefs in {brief.CACHE_FILE}")


if __name__ == "__main__":
    main()
