"""LLM answer with forced JSON citations, then verified."""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field

from core.llm import complete_json
from core.rerank import rerank
from core.retrieve import Hit, retrieve
from core.verify import verify_citations

SYSTEM = """You are an equity research assistant answering ONLY from the provided excerpts of a listed Indian company's regulatory filings.
Rules:
- Every factual claim must be supported by a citation to one of the excerpts [c1], [c2], ...
- A citation is {"ref": "<c-id>", "quote": "<verbatim substring of that excerpt, 8-40 words>"}.
- Quotes must be copied exactly from the excerpt text. Do not paraphrase inside "quote".
- If the excerpts do not contain the answer, set "answer" to "Not found in the filings available." and "citations" to [].
- Be concise. Numbers must match the excerpt exactly, including units and period.
Return JSON: {"answer": str, "citations": [{"ref": str, "quote": str}], "confidence": "high"|"medium"|"low"}"""

# v2: v1 refused a "why" question whose cause was spread across excerpts — no single contiguous
# quote stated it, and refusal was the safe branch. v2 allows assembling an answer from several
# excerpts and narrows refusal to "nothing relevant". Selected per call or via PROMPT_VERSION.
SYSTEM_V2 = """You are an equity research assistant answering ONLY from the provided excerpts of a listed Indian company's regulatory filings.
Rules:
- Every factual claim must be supported by a citation to one of the excerpts [c1], [c2], ...
- A citation is {"ref": "<c-id>", "quote": "<verbatim substring of that excerpt, 8-60 words>"}.
- Quotes must be copied exactly from the excerpt text. Do not paraphrase inside "quote".
- A question asking why or how may require combining several excerpts. Answer it and cite every excerpt you used;
  each quote need only support the part of the answer it is cited for, not the whole claim.
- Refuse ONLY if the excerpts contain nothing relevant to the question: then set "answer" to
  "Not found in the filings available." and "citations" to []. If they support part of the answer, give that part
  and state plainly what the filings do not say.
- Be concise. Numbers must match the excerpt exactly, including units and period.
Return JSON: {"answer": str, "citations": [{"ref": str, "quote": str}], "confidence": "high"|"medium"|"low"}"""

PROMPTS = {"v1": SYSTEM, "v2": SYSTEM_V2}


@dataclass
class Citation:
    ref: str
    quote: str
    document_id: int
    title: str
    doc_type: str
    page: int
    verified: bool
    fiscal_period: str | None = None


@dataclass
class Answer:
    question: str
    symbol: str
    answer: str
    confidence: str
    citations: list[Citation] = field(default_factory=list)
    dropped_citations: int = 0
    abstained: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def _format_excerpts(hits: list[Hit]) -> str:
    out = []
    for i, h in enumerate(hits, start=1):
        meta = f"{h.doc_type} | {h.title[:80]} | period={h.fiscal_period or '?'} | page {h.page}"
        out.append(f"[c{i}] ({meta})\n{h.text}")
    return "\n\n".join(out)


def answer_with_trace(symbol: str, question: str, doc_type: str | None = None, mode: str = "hybrid",
                      use_rerank: bool | None = None, prompt: str | None = None) -> tuple[Answer, list[Hit], list[dict]]:
    """ask() plus what the eval needs: the excerpts the LLM saw and its raw (pre-verifier) citations.
    mode: hybrid | bm25 | vector. use_rerank: None = follow the RERANK env setting.
    prompt: v1 | v2; None = PROMPT_VERSION env var (default v1)."""
    system = PROMPTS[prompt or os.environ.get("PROMPT_VERSION", "v1")]
    hits = retrieve(symbol, question, doc_type=doc_type, mode=mode)
    hits = rerank(question, hits, enabled=use_rerank)
    if not hits:
        return Answer(question, symbol, "Not found in the filings available.", "low", abstained=True), [], []
    raw = complete_json(system, f"Company: {symbol}\nQuestion: {question}\n\nExcerpts:\n{_format_excerpts(hits)}")
    raw_cits = [c for c in raw.get("citations", []) if isinstance(c, dict)]
    ref_map = {f"c{i}": h for i, h in enumerate(hits, start=1)}
    cits, dropped = verify_citations(raw_cits, ref_map)
    ans = Answer(question, symbol, str(raw.get("answer", "")).strip(), raw.get("confidence", "low"), cits, dropped)
    ans.abstained = ans.answer.lower().startswith("not found") or (not cits and bool(raw_cits))
    if raw_cits and not cits:
        # LLM claimed support but nothing verified → downgrade loudly
        ans.answer = "Unverified: the model's citations did not match the filings. " + ans.answer
        ans.confidence = "low"
    return ans, hits, raw_cits


def ask(symbol: str, question: str, doc_type: str | None = None) -> Answer:
    return answer_with_trace(symbol, question, doc_type)[0]
