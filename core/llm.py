"""Thin provider-agnostic LLM wrapper. All calls return parsed JSON."""
from __future__ import annotations

import json
import re

from core.config import settings


def _strip_fences(s: str) -> str:
    s = s.strip()
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    return s


def _gemini(system: str, user: str) -> str:
    """Try LLM_MODEL, then each LLM_FALLBACK_MODELS entry. No silent SDK retries: an overloaded
    model (503/504/429) fails in seconds and we move on, instead of backing off for minutes."""
    import google.generativeai as genai

    genai.configure(api_key=settings.llm_api_key)
    models = [settings.llm_model] + [m.strip() for m in settings.llm_fallback_models.split(",") if m.strip()]
    last: Exception | None = None
    for name in models:
        model = genai.GenerativeModel(name, system_instruction=system,
                                      generation_config={"response_mime_type": "application/json", "temperature": 0})
        try:
            return model.generate_content(user, request_options={"retry": None, "timeout": settings.llm_timeout_s}).text
        except Exception as e:  # noqa: BLE001
            code = getattr(e, "code", None)
            transient = code in (429, 500, 503, 504) or type(e).__name__ in {
                "ServiceUnavailable", "DeadlineExceeded", "ResourceExhausted", "InternalServerError"}
            if not transient:
                raise
            print(f"[llm] {name} unavailable ({type(e).__name__}); trying next model")
            last = e
    raise RuntimeError(f"all Gemini models failed; last error: {last}")


def _anthropic(system: str, user: str) -> str:
    import anthropic

    client = anthropic.Anthropic(api_key=settings.llm_api_key)
    msg = client.messages.create(model=settings.llm_model, max_tokens=2048, temperature=0, system=system,
                                 messages=[{"role": "user", "content": user}])
    return msg.content[0].text


def complete_json(system: str, user: str) -> dict:
    raw = _gemini(system, user) if settings.llm_provider == "gemini" else _anthropic(system, user)
    try:
        return json.loads(_strip_fences(raw))
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, re.S)
        if not m:
            raise
        return json.loads(m.group(0))
