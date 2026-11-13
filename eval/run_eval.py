"""
Eval runner. Metrics:
  - answer_accuracy: LLM-judge (correct/incorrect vs gold) on answerable Qs; spot-check 20% by hand.
  - citation_precision: fraction of surviving citations whose (doc_type, page) matches gold page ±1.
  - abstention_rate: fraction of unanswerable Qs where the system abstained.
  - false_abstention: fraction of answerable Qs where the system abstained.
Writes eval/results.jsonl and prints a markdown table for the README.
"""
from __future__ import annotations

import json
from pathlib import Path

from core.answer import ask
from core.llm import complete_json

HERE = Path(__file__).parent
JUDGE = """You grade a research assistant's answer against a gold answer. Return JSON {"correct": true|false, "reason": str}.
Correct means the same facts/numbers/direction; wording may differ. Numbers must match in value and unit."""


def judge(question: str, gold: str, pred: str) -> bool:
    j = complete_json(JUDGE, f"Question: {question}\nGold: {gold}\nPredicted: {pred}")
    return bool(j.get("correct"))


def main() -> None:
    qs = [json.loads(l) for l in (HERE / "questions.jsonl").read_text().splitlines() if l.strip()]
    results = []
    for q in qs:
        a = ask(q["symbol"], q["question"])
        correct = judge(q["question"], q["gold_answer"], a.answer) if q["answerable"] else None
        cit_hits = [c for c in a.citations if q.get("gold_page") and c.doc_type == q["gold_doc_type"] and abs(c.page - q["gold_page"]) <= 1]
        results.append({
            "id": q["id"], "type": q["type"], "answerable": q["answerable"], "abstained": a.abstained,
            "correct": correct, "n_cit": len(a.citations), "n_cit_hit": len(cit_hits), "dropped": a.dropped_citations,
            "pred": a.answer,
        })
    (HERE / "results.jsonl").write_text("\n".join(json.dumps(r) for r in results))

    ans = [r for r in results if r["answerable"]]
    unans = [r for r in results if not r["answerable"]]
    acc = sum(1 for r in ans if r["correct"]) / max(1, len(ans))
    cits = sum(r["n_cit"] for r in ans)
    cprec = sum(r["n_cit_hit"] for r in ans) / max(1, cits)
    abst = sum(1 for r in unans if r["abstained"]) / max(1, len(unans))
    fabst = sum(1 for r in ans if r["abstained"]) / max(1, len(ans))
    print("| Metric | Value |\n|---|---|")
    print(f"| Answer accuracy ({len(ans)} answerable) | {acc:.0%} |")
    print(f"| Citation precision ({cits} citations) | {cprec:.0%} |")
    print(f"| Abstention on unanswerable ({len(unans)}) | {abst:.0%} |")
    print(f"| False abstention on answerable | {fabst:.0%} |")


if __name__ == "__main__":
    main()
