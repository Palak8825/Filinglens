"""LLM answer with forced JSON citations, then verified."""
from __future__ import annotations

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


def ask(symbol: str, question: str, doc_type: str | None = None) -> Answer:
    hits = rerank(question, retrieve(symbol, question, doc_type=doc_type))
    if not hits:
        return Answer(question, symbol, "Not found in the filings available.", "low", abstained=True)
    raw = complete_json(SYSTEM, f"Company: {symbol}\nQuestion: {question}\n\nExcerpts:\n{_format_excerpts(hits)}")
    ref_map = {f"c{i}": h for i, h in enumerate(hits, start=1)}
    cits, dropped = verify_citations(raw.get("citations", []), ref_map)
    ans = Answer(question, symbol, raw.get("answer", "").strip(), raw.get("confidence", "low"), cits, dropped)
    ans.abstained = ans.answer.lower().startswith("not found") or (not cits and bool(raw.get("citations")))
    if raw.get("citations") and not cits:
        # LLM claimed support but nothing verified → downgrade loudly
        ans.answer = "Unverified: the model's citations did not match the filings. " + ans.answer
        ans.confidence = "low"
    return ans
