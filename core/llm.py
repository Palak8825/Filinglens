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
    import google.generativeai as genai

    genai.configure(api_key=settings.llm_api_key)
    model = genai.GenerativeModel(settings.llm_model, system_instruction=system,
                                  generation_config={"response_mime_type": "application/json", "temperature": 0})
    return model.generate_content(user).text


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
