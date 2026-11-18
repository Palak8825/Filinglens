"""
FilingLens eval. Two parts:

1. Retrieval ablation (no LLM, deterministic):
     for each mode — bm25 | vector | hybrid | hybrid+rerank — does a gold (document, page) reach the
     top-8 excerpts the LLM actually sees?  hit@8, MRR, and for cross_doc questions "all gold docs
     present".  Broken down by question type.
2. End-to-end, per config (answer + verify + judge):
     answer accuracy        LLM judge vs gold_answer (+ deterministic must_include check where given)
     citation precision     verified citations landing on a gold (document, page)
     abstention             unanswerable questions correctly refused
     false abstention       answerable questions wrongly refused
     verifier rejection     share of the model's raw citations the verifier dropped ("hallucination rate")
     precision w/o verifier citation precision if raw citations had been shown unchecked

Questions: eval/questions.jsonl, one JSON object per line:
  {"id", "symbol", "type": factual|reasoning|cross_doc|flag|unanswerable, "question",
   "gold_answer", "must_include": [optional strings], "answerable": bool,
   "gold": [{"path": "<document path as in data/raw>", "page": <1-based PDF page>}, ...]}
  "gold" lists every acceptable (document, page); cross_doc questions list one per required document
  and add "gold_all": true.

Usage
  python -m eval.run_eval                         # retrieval ablation + end-to-end on hybrid+rerank (production)
  python -m eval.run_eval --configs hybrid+rerank,hybrid,hybrid+rerank@weak,hybrid+rerank@v2
  python -m eval.run_eval --retrieval-only
Answers are cached in eval/cache/<config>.jsonl (delete to re-run). Report → eval/report.md.
The judge is settings.judge_model (JUDGE_MODEL), never the answering model; cached verdicts from a
different judge are re-graded without re-answering. Cells over fewer than 5 questions print counts.
"""
from __future__ import annotations

import argparse
import json
import re
import time
from collections import defaultdict
from pathlib import Path

from sqlalchemy import select

from core.answer import answer_with_trace
from core.config import settings
from core.db import Document, get_session
from core.llm import complete_json
from core.rerank import rerank
from core.retrieve import retrieve

HERE = Path(__file__).parent
CACHE = HERE / "cache"
TYPES = ["factual", "reasoning", "cross_doc", "flag", "unanswerable"]
CONFIGS = {  # name -> (retrieval mode, rerank, prompt version, model override or None = settings.llm_model)
    "bm25": ("bm25", False, "v1", None),
    "vector": ("vector", False, "v1", None),
    "hybrid": ("hybrid", False, "v1", None),
    "hybrid+rerank": ("hybrid", True, "v1", None),
    "hybrid@v2": ("hybrid", False, "v2", None),                 # prompt arm: same model, relaxed prompt
    "hybrid@flash": ("hybrid", False, "v1", "gemini-3.5-flash"),  # model arm: same prompt, bigger model
    "hybrid@v2+flash": ("hybrid", False, "v2", "gemini-3.5-flash"),
    # Model arms. gemini-3.1-flash-lite was first run here as a "weak" arm and outscored 3.5-flash-lite
    # (20/20 vs 16/20, 0 vs 2 false refusals), so it became the production model on 2026-11-18.
    "hybrid+rerank@3.5lite": ("hybrid", True, "v1", "gemini-3.5-flash-lite"),
    "hybrid@3.5lite": ("hybrid", False, "v1", "gemini-3.5-flash-lite"),
    "hybrid+rerank@v2": ("hybrid", True, "v2", None),
    "hybrid+rerank@flash": ("hybrid", True, "v1", "gemini-3.5-flash"),  # model arm on the production pipeline
    # Escalate on refusal: production model first; gemini-3.5-flash only when it refuses with passages in hand,
    # kept only if verified. Judge on accuracy AND correct abstention (escalation must not invent answers).
    "hybrid+rerank+esc": ("hybrid", True, "v1", None),
}
HEADLINE = "hybrid+rerank"  # what the app runs by default (RERANK=1)
JUDGE = """You grade a research assistant's answer against a gold answer from a company's filings.
Return JSON {"correct": true|false, "reason": str}.
Correct = states the same key facts as the gold answer: numbers must match in value, unit and period;
direction and causes must match. Extra correct detail is fine. A refusal ("Not found...") is incorrect
when a gold answer exists. Wording may differ."""


def load_questions() -> list[dict]:
    qs = []
    for i, line in enumerate((HERE / "questions.jsonl").read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("//"):
            continue
        q = json.loads(line)
        if q.get("symbol") == "EXAMPLE":
            continue
        q.setdefault("gold", [])
        qs.append(q)
    return qs


_doc_paths: dict[int, str] = {}


def doc_path(document_id: int) -> str:
    if not _doc_paths:
        with get_session() as s:
            _doc_paths.update({i: p for i, p in s.execute(select(Document.id, Document.path)).all()})
    return _norm_path(_doc_paths.get(document_id, ""))


def _norm_path(p: str) -> str:
    return str(p).replace("/", "\\").lower().lstrip(".\\")


_path_period: dict[str, str] = {}


def period_of(path: str) -> str:
    """Fiscal period of a document (by normalised path); the path itself when the period is unknown."""
    if not _path_period:
        with get_session() as s:
            _path_period.update({_norm_path(p): (fp or _norm_path(p))
                                 for p, fp in s.execute(select(Document.path, Document.fiscal_period)).all()})
    return _path_period.get(_norm_path(path), _norm_path(path))


def covers_all_periods(gold: set[tuple[str, int]], found_paths: set[str]) -> bool:
    """cross_doc success = at least one gold document from EVERY fiscal period the gold spans. Counting
    distinct files instead made it unreachable: the corpus holds byte-identical duplicate filings (e.g. three
    copies of one TCS results PDF) and retrieval de-duplicates identical text, so only one copy can appear."""
    need = {period_of(p) for p, _ in gold}
    return bool(gold) and need <= {period_of(p) for p in found_paths}


def gold_set(q: dict) -> set[tuple[str, int]]:
    return {(_norm_path(g["path"]), int(g["page"])) for g in q.get("gold", []) if g.get("path") and g.get("page")}


def _evidence_ok(q: dict, text: str) -> bool:
    """A retrieved chunk counts only if it contains one of the question's evidence strings (when given).
    Page-level matching alone over-credits: a page holds several chunks, and the neighbour of the
    answer chunk is not the answer."""
    ev = q.get("evidence") or []
    if not ev:
        return True
    t = re.sub(r"\s+", " ", text).lower()
    return any(re.sub(r"\s+", " ", e).lower() in t for e in ev)


def _rank_of_gold(q: dict, hits) -> tuple[int | None, bool]:
    """(1-based rank of first gold hit, whether every gold document is present)."""
    gold = gold_set(q)
    if not gold:
        return None, False
    first = None
    docs_needed = {p for p, _ in gold}
    docs_seen: set[str] = set()
    for rank, h in enumerate(hits, 1):
        key = (doc_path(h.document_id), h.page)
        if key in gold and _evidence_ok(q, h.text):
            first = first or rank
            docs_seen.add(key[0])
    return first, covers_all_periods(gold, docs_seen)


# ---------------------------------------------------------------- retrieval ablation
def retrieval_ablation(qs: list[dict], configs: list[str]) -> dict:
    rows: dict[str, dict] = {}
    answerable = [q for q in qs if q["answerable"] and gold_set(q)]
    for name in configs:
        mode, rr = CONFIGS[name][:2]
        per_type: dict[str, list] = defaultdict(list)
        for q in answerable:
            hits = rerank(q["question"], retrieve(q["symbol"], q["question"], mode=mode), enabled=rr)
            rank, all_docs = _rank_of_gold(q, hits)
            per_type[q["type"]].append((rank, all_docs))
            per_type["all"].append((rank, all_docs))
        rows[name] = {
            t: {
                "n": len(v),
                "hits": sum(1 for r, _ in v if r),
                "all_gold": sum(1 for _, a in v if a),
                "hit@8": sum(1 for r, _ in v if r) / len(v),
                "mrr": sum(1 / r for r, _ in v if r) / len(v),
                "all_gold@8": sum(1 for _, a in v if a) / len(v),
            } for t, v in per_type.items() if v
        }
    return rows


# ---------------------------------------------------------------- end-to-end
def _llm_retry(fn, *a, tries: int = 6):
    for i in range(tries):
        try:
            return fn(*a)
        except Exception as e:  # noqa: BLE001 - rate limits / 503s: back off, same model
            if i == tries - 1:
                raise
            wait = 10 * (i + 1)
            print(f"    [retry in {wait}s] {type(e).__name__}: {str(e)[:100]}")
            time.sleep(wait)


JUDGE_MODEL = settings.judge_model  # fixed across configs and different from the answering model


def judge(question: str, gold: str, pred: str) -> tuple[bool, str]:
    run_model, settings.llm_model = settings.llm_model, JUDGE_MODEL
    try:
        j = _llm_retry(complete_json, JUDGE, f"Question: {question}\nGold answer: {gold}\nPredicted answer: {pred}")
    finally:
        settings.llm_model = run_model
    return bool(j.get("correct")), str(j.get("reason", ""))[:300]


PENDING = "PENDING"
DEFER_JUDGE = False  # --defer-judge: save answers now, grade on a later run (e.g. judge model out of quota)
REDO: set[str] = set()  # --redo id,id: re-answer these questions even if cached (after a pipeline change)


def safe_judge(question: str, gold: str, pred: str) -> tuple[bool | None, str, str]:
    """judge() that never loses work: if the judge model is down (503/504/429 after retries), the answer is
    kept with correct=None and judge_model=PENDING, and the next run grades it without re-answering."""
    if DEFER_JUDGE:
        return None, "", PENDING
    try:
        ok, reason = judge(question, gold, pred)
        return ok, reason, JUDGE_MODEL
    except Exception as e:  # noqa: BLE001
        print(f"    [judge unavailable, grading deferred] {type(e).__name__}: {str(e)[:90]}")
        return None, "", PENDING


def _must_include_ok(q: dict, pred: str) -> bool | None:
    keys = q.get("must_include") or []
    if not keys:
        return None
    norm = lambda s: re.sub(r"[\s,]+", "", s.lower())  # noqa: E731
    return all(norm(k) in norm(pred) for k in keys)


def _chunk_texts(ids: list[int]) -> dict[int, str]:
    from core.db import Chunk

    if not ids:
        return {}
    with get_session() as s:
        return {i: t for i, t in s.execute(select(Chunk.id, Chunk.text).where(Chunk.id.in_(ids))).all()}


def _rescore(r: dict, q: dict) -> dict:
    """Recompute every gold-dependent field from stored locations, so editing gold re-scores cached runs."""
    gold = gold_set(q)
    as_keys = lambda xs: [(_norm_path(p), int(pg)) for p, pg in xs if p is not None]  # noqa: E731
    raw, ver, hits = as_keys(r.get("raw_keys", [])), as_keys(r.get("ver_keys", [])), as_keys(r.get("hit_keys", []))
    r["raw_on_gold"] = sum(1 for k in raw if k in gold)
    r["verified_on_gold"] = sum(1 for k in ver if k in gold)
    texts = _chunk_texts(r.get("hit_ids", [])) if q.get("evidence") else {}
    ok = [k in gold and (not q.get("evidence") or _evidence_ok(q, texts.get(cid, "")))
          for k, cid in zip(hits, r.get("hit_ids") or [None] * len(hits))]
    r["gold_rank"] = next((i for i, good in enumerate(ok, 1) if good), None)
    r["gold_all_docs"] = covers_all_periods(gold, {k[0] for k, good in zip(hits, ok) if good})
    r["type"], r["answerable"] = q["type"], q["answerable"]
    return r


def run_config(qs: list[dict], name: str, pace: float = 0.0) -> list[dict]:
    mode, rr, prompt, model = CONFIGS[name]
    base_model, base_esc = settings.llm_model, settings.escalate_model
    settings.llm_model = model or base_model  # answers only; judge() pins JUDGE_MODEL
    settings.escalate_model = "gemini-3.5-flash" if name.endswith("+esc") else ""
    try:
        return _run_config(qs, name, mode, rr, prompt, pace)
    finally:
        settings.llm_model, settings.escalate_model = base_model, base_esc


def _run_config(qs: list[dict], name: str, mode: str, rr: bool, prompt: str, pace: float) -> list[dict]:
    CACHE.mkdir(exist_ok=True)
    path = CACHE / f"{name}.jsonl"
    done = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                done[r["id"]] = r
    out = []
    rejudged = False
    for q in qs:
        cached = done.get(q["id"])
        if cached and q["id"] not in REDO and cached.get("question") == q["question"] \
                and cached.get("gold_answer") == q.get("gold_answer") and "hit_ids" in cached:
            if q["answerable"] and cached.get("judge_model") != JUDGE_MODEL:
                # Graded by another model (older runs used the answering model as judge): re-grade only.
                cached["correct"], cached["judge_reason"], cached["judge_model"] = safe_judge(
                    q["question"], q["gold_answer"], cached["answer"])
                if cached["judge_model"] != PENDING:
                    print(f"  [{name}] {q['id']} judged with {JUDGE_MODEL}: {'ok' if cached['correct'] else 'BAD'}")
                rejudged = True
            out.append(_rescore(cached, q))
            continue
        t = time.time()
        ans, hits, raw = _llm_retry(answer_with_trace, q["symbol"], q["question"], None, mode, rr, prompt)
        latency = time.time() - t
        ref_page = {f"c{i}": (doc_path(h.document_id), h.page) for i, h in enumerate(hits, 1)}
        raw_keys = [ref_page.get(str(c.get("ref")), (None, 0)) for c in raw]
        correct, reason, jmodel = (None, "", None)
        if q["answerable"]:
            correct, reason, jmodel = safe_judge(q["question"], q["gold_answer"], ans.answer)
        r = {
            "id": q["id"], "symbol": q["symbol"], "question": q["question"], "gold_answer": q.get("gold_answer"),
            "abstained": ans.abstained, "correct": correct, "judge_reason": reason,
            "judge_model": jmodel, "answer_model": ans.model, "abstain_source": ans.abstain_source, "escalated": ans.escalated,
            "must_include_ok": _must_include_ok(q, ans.answer) if q["answerable"] else None,
            "n_raw": len(raw), "n_verified": len(ans.citations), "dropped": ans.dropped_citations,
            "raw_keys": raw_keys, "ver_keys": [(doc_path(c.document_id), c.page) for c in ans.citations],
            "hit_keys": [(doc_path(h.document_id), h.page) for h in hits], "hit_ids": [h.chunk_id for h in hits],
            "latency_s": round(latency, 2), "answer": ans.answer,
            "citations": [{"page": c.page, "path": doc_path(c.document_id), "quote": c.quote[:200]} for c in ans.citations],
        }
        r = _rescore(r, q)
        out.append(r)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
        if pace:
            time.sleep(pace)
        mark = "abst" if r["abstained"] else ("ok " if correct else "BAD" if correct is False else "-  ")
        print(f"  [{name}] {q['id']} {mark} {latency:4.1f}s cits {r['n_verified']}/{r['n_raw']}  {q['question'][:70]}")
    if rejudged:  # persist the new verdicts: rewrite the cache file with every row, old and fresh
        keep = {r["id"]: r for r in done.values()}
        keep.update({r["id"]: r for r in out})
        path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in keep.values()), encoding="utf-8")
    return out


def summarize(results: list[dict]) -> dict:
    ans_all = [r for r in results if r["answerable"]]
    ans = [r for r in ans_all if r.get("judge_model") != PENDING]  # accuracy only over graded answers
    unans = [r for r in results if not r["answerable"]]
    raw = sum(r["n_raw"] for r in results)
    ver = sum(r["n_verified"] for r in ans)
    raw_ans = sum(r["n_raw"] for r in ans)
    pct = lambda a, b: {"k": a, "n": b, "v": a / b if b else float("nan")}  # noqa: E731
    mi = [r for r in ans if r["must_include_ok"] is not None]
    return {
        "n_answerable": len(ans), "n_unanswerable": len(unans), "n_pending_judge": len(ans_all) - len(ans),
        "accuracy": pct(sum(1 for r in ans if r["correct"]), len(ans)),
        "accuracy_by_type": {t: pct(sum(1 for r in ans if r["type"] == t and r["correct"]),
                                    sum(1 for r in ans if r["type"] == t)) for t in TYPES if t != "unanswerable"
                             and any(r["type"] == t for r in ans)},
        "must_include_agree": pct(sum(1 for r in mi if r["must_include_ok"] == bool(r["correct"])), len(mi)),
        "citation_precision": pct(sum(r["verified_on_gold"] for r in ans), ver),
        "citation_precision_no_verifier": pct(sum(r["raw_on_gold"] for r in ans), raw_ans),
        "verifier_rejection": pct(sum(r["dropped"] for r in results), raw),
        "n_raw_citations": raw,
        "abstention": pct(sum(1 for r in unans if r["abstained"]), len(unans)),
        # Split by who refused: the period guard (a rule, no model involved) or the model itself. Without the split
        # the report would credit the model for refusals a deterministic rule produced.
        "abstention_by_rule": pct(sum(1 for r in unans if r["abstained"] and r.get("abstain_source") == "period_guard"),
                                  len(unans)),
        "false_abstention": pct(sum(1 for r in ans if r["abstained"]), len(ans)),
        "unsupported_answers": pct(sum(1 for r in ans if not r["abstained"] and r["n_verified"] == 0), len(ans)),
        "median_latency_s": sorted(r["latency_s"] for r in results)[len(results) // 2] if results else float("nan"),
    }


def fmt(x) -> str:
    """A {"k","n","v"} cell prints counts when n < 5 (a percentage over 1-4 questions overstates what it
    shows), otherwise "pct (k/n)"."""
    if isinstance(x, dict):
        k, n = x["k"], x["n"]
        if not n:
            return "—"
        return f"{k}/{n}" if n < 5 else f"{k / n:.0%} ({k}/{n})"
    return "—" if x != x else f"{x:.0%}"


def report(qs: list[dict], abl: dict | None, e2e: dict[str, dict]) -> str:
    counts = defaultdict(int)
    for q in qs:
        counts[q["type"]] += 1
    lines = [f"# FilingLens eval\n\nAnswering model: `{settings.llm_model}` · judge: `{JUDGE_MODEL}` · embeddings `{settings.embed_model}` · "
             f"{len(qs)} questions: " + ", ".join(f"{counts[t]} {t}" for t in TYPES if counts[t]) + "\n"]
    if e2e:
        head = HEADLINE if HEADLINE in e2e else next(iter(e2e))
        main = e2e[head]
        lines += [f"## Headline ({head}, verifier on)\n", "| Metric | Value |", "|---|---|",
                  f"| Answer accuracy ({main['n_answerable']} answerable) | {fmt(main['accuracy'])} |",
                  *[f"| · {t} | {fmt(v)} |" for t, v in main["accuracy_by_type"].items()],
                  f"| Citation precision (verified citations on a gold page) | {fmt(main['citation_precision'])} |",
                  f"| Citation precision if unverified citations were shown | {fmt(main['citation_precision_no_verifier'])} |",
                  f"| Model citations rejected by the verifier | {fmt(main['verifier_rejection'])} |",
                  f"| Correct abstention ({main['n_unanswerable']} unanswerable) | {fmt(main['abstention'])} |",
                  f"| · of which by the period guard (rule, not the model) | {main.get('abstention_by_rule', {}).get('k', 0)} |",
                  f"| False abstention on answerable | {fmt(main['false_abstention'])} |",
                  f"| Answers with no verified citation | {fmt(main['unsupported_answers'])} |",
                  f"| Median latency | {main['median_latency_s']:.1f}s |", ""]
    if abl:
        types = [t for t in ["all", "factual", "reasoning", "cross_doc", "flag"] if any(t in v for v in abl.values())]
        lines += ["## Retrieval ablation — gold page in the 8 excerpts the LLM sees (hit@8 / MRR)\n",
                  "| Config | " + " | ".join(types) + " | cross_doc: all docs |",
                  "|---|" + "---|" * (len(types) + 1)]
        for name, v in abl.items():
            cells = [fmt({"k": v[t]["hits"], "n": v[t]["n"]}) + f" / {v[t]['mrr']:.2f}" if t in v else "—"
                     for t in types]
            xd = fmt({"k": v["cross_doc"]["all_gold"], "n": v["cross_doc"]["n"]}) if "cross_doc" in v else "—"
            lines.append(f"| {name} | " + " | ".join(cells) + f" | {xd} |")
        lines.append("")
    if len(e2e) > 1:
        lines += ["## End-to-end by config\n",
                  "| Config | Prompt | Model | Accuracy | Reasoning acc. | Citation precision | Verifier rejected | Abstention | False abstention |",
                  "|---|---|---|---|---|---|---|---|---|"]
        for name, m in e2e.items():
            _, _, prompt, model = CONFIGS[name]
            lines.append(f"| {name} | {prompt} | {model or settings.llm_model} | {fmt(m['accuracy'])} | "
                         f"{fmt(m['accuracy_by_type'].get('reasoning', {'k': 0, 'n': 0}))} | {fmt(m['citation_precision'])} | "
                         f"{fmt(m['verifier_rejection'])} | {fmt(m['abstention'])} | {fmt(m['false_abstention'])} |")
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--configs", default=HEADLINE, help=f"comma-separated, from {list(CONFIGS)}")
    p.add_argument("--ablation", default="bm25,vector,hybrid,hybrid+rerank",
                   help="retrieval-ablation configs ('' to skip)")
    p.add_argument("--no-ablation", action="store_true", help="skip the retrieval ablation")
    p.add_argument("--retrieval-only", action="store_true")
    p.add_argument("--redo", default="", help="comma-separated question ids to re-answer even if cached")
    p.add_argument("--defer-judge", action="store_true",
                   help="generate and save answers without grading; a later run grades them (judge model unavailable)")
    p.add_argument("--pace", type=float, default=2.0, help="seconds between questions (free-tier rate limits)")
    a = p.parse_args()
    global DEFER_JUDGE, REDO
    DEFER_JUDGE = a.defer_judge
    REDO = {x.strip() for x in a.redo.split(",") if x.strip()}
    settings.llm_fallback_models = ""  # one model per run: a silent fallback would contaminate the comparison
    for name in [c for c in a.configs.split(",") if c]:
        if (CONFIGS[name][3] or settings.llm_model) == JUDGE_MODEL:
            print(f"WARNING: arm {name} answers with the judge model ({JUDGE_MODEL}); set JUDGE_MODEL to another model")

    qs = load_questions()
    if not qs:
        raise SystemExit("eval/questions.jsonl has no real questions yet (EXAMPLE rows are skipped).")
    abl = None if a.no_ablation or not a.ablation else retrieval_ablation(qs, [c for c in a.ablation.split(",") if c])
    e2e = {}
    if not a.retrieval_only:
        for name in [c for c in a.configs.split(",") if c]:
            e2e[name] = summarize(run_config(qs, name, a.pace))
    pending = sum(m.get("n_pending_judge", 0) for m in e2e.values())
    if pending:
        print(f"WARNING: {pending} answer(s) not yet graded (judge model unavailable); re-run to grade them. "
              "Accuracy below excludes them.")
    md = report(qs, abl, e2e)
    (HERE / "report.md").write_text(md, encoding="utf-8")
    (HERE / "report.json").write_text(json.dumps({"ablation": abl, "e2e": e2e}, indent=2), encoding="utf-8")
    print("\n" + md)


if __name__ == "__main__":
    main()
