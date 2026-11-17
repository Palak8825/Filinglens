"""LLM answer with forced JSON citations, then verified."""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field

from core.config import settings
from core.llm import complete_json_with_model, has_api_key
from core.rerank import rerank
from core.retrieve import Hit, resolve_periods, retrieve, uncovered_period
from core.verify import verify_citations


class NoAPIKey(RuntimeError):
    """No LLM key configured and the question isn't in the demo cache."""

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
    model: str | None = None        # the model that actually answered (after any fallback)
    period_note: str | None = None  # set when "latest quarter" etc. was resolved to a filed quarter
    prompt: str = "v1"
    abstain_source: str | None = None  # "period_guard" (rule) or "model" when abstained; None otherwise
    escalated: bool = False            # answered by settings.escalate_model after the first model refused

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
    prompt = prompt or os.environ.get("PROMPT_VERSION", "v1")
    system = PROMPTS[prompt]
    guard = uncovered_period(symbol, question)
    if guard:
        # The question is about a quarter no indexed filing covers. Without this, retrieval fell back to all
        # filings and the model once attached a real Q1 FY24 quote to "Q1 FY22", which the verifier passed
        # (it checks a quote is present, not which period it describes). Answer with the boundary instead.
        return Answer(question, symbol, guard, "high", abstained=True, prompt=prompt,
                      abstain_source="period_guard"), [], []
    _, period_note = resolve_periods(symbol, question)
    hits = retrieve(symbol, question, doc_type=doc_type, mode=mode)
    hits = rerank(question, hits, enabled=use_rerank)
    if not hits:
        return Answer(question, symbol, "Not found in the filings available.", "low", abstained=True,
                      period_note=period_note, prompt=prompt, abstain_source="model"), [], []
    raw, model = complete_json_with_model(
        system, f"Company: {symbol}\nQuestion: {question}\n\nExcerpts:\n{_format_excerpts(hits)}")
    raw_cits = [c for c in raw.get("citations", []) if isinstance(c, dict)]
    ref_map = {f"c{i}": h for i, h in enumerate(hits, start=1)}
    cits, dropped = verify_citations(raw_cits, ref_map)
    ans = Answer(question, symbol, str(raw.get("answer", "")).strip(), raw.get("confidence", "low"), cits, dropped,
                 model=model, period_note=period_note, prompt=prompt)
    ans.abstained = ans.answer.lower().startswith("not found") or (not cits and bool(raw_cits))
    ans.abstain_source = "model" if ans.abstained else None
    if ans.abstained and settings.escalate_model:
        esc = _escalate(system, symbol, question, hits, ref_map, prompt, period_note)
        if esc is not None:
            return esc, hits, raw_cits
    if raw_cits and not cits:
        # LLM claimed support but nothing verified → downgrade loudly
        ans.answer = "Unverified: the model's citations did not match the filings. " + ans.answer
        ans.confidence = "low"
    return ans, hits, raw_cits


def _escalate(system: str, symbol: str, question: str, hits: list[Hit], ref_map: dict, prompt: str,
              period_note: str | None) -> Answer | None:
    """One call to the stronger model on the same excerpts. Kept only if it is a real answer with at least one
    verified citation: escalation can turn a refusal into a cited answer, never into an uncited assertion."""
    try:
        raw, model = complete_json_with_model(
            system, f"Company: {symbol}\nQuestion: {question}\n\nExcerpts:\n{_format_excerpts(hits)}",
            only=settings.escalate_model)
    except Exception as e:  # noqa: BLE001 - stronger model unavailable: the refusal stands
        print(f"[escalate] {settings.escalate_model} unavailable: {type(e).__name__}")
        return None
    raw_cits = [c for c in raw.get("citations", []) if isinstance(c, dict)]
    cits, dropped = verify_citations(raw_cits, ref_map)
    text = str(raw.get("answer", "")).strip()
    if not cits or text.lower().startswith("not found"):
        return None
    return Answer(question, symbol, text, raw.get("confidence", "low"), cits, dropped, model=model,
                  period_note=period_note, prompt=prompt, escalated=True)


def ask(symbol: str, question: str, doc_type: str | None = None) -> Answer:
    return answer_with_trace(symbol, question, doc_type)[0]


def ask_dict(symbol: str, question: str, doc_type: str | None = None, use_cache: bool = True) -> dict:
    """What the API serves. The demo cache is consulted first (only for unrestricted questions, which
    is how the cache is built); a miss falls straight through to the live path, so with an API key the
    worst case is the same as no cache. Without a key, a miss raises NoAPIKey."""
    from core import answer_cache

    key = has_api_key()
    prompt = os.environ.get("PROMPT_VERSION", "v1")
    if use_cache and doc_type is None:
        hit = answer_cache.get(symbol, question, prompt, key)
        if hit:
            return hit
    if not key:
        raise NoAPIKey("No LLM_API_KEY set. Cached example questions still work; add a key in .env to ask your own.")
    return {**ask(symbol, question, doc_type).to_dict(), "cached": None}
