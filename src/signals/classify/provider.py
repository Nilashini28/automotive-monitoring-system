"""LLM provider behind one interface: OpenAI-compatible chat + Ollama.

Anything exposing `/v1/chat/completions` works (OpenAI, Groq, Together,
OpenRouter, an in-cluster vLLM), so the model is a config value, not a code
change. Response parsing is strict-JSON-first with a fenced-block fallback
because small models habitually wrap output in markdown.
"""

from __future__ import annotations

import json
import re

import httpx

from signals.classify.prompts import build_messages
from signals.classify.rules import classify as rules_classify
from signals.classify.schema import CategoryScore, Verdict
from signals.config import get_settings
from signals.logging_setup import get_logger

log = get_logger(__name__)

_FENCE = re.compile(r"```(?:json)?\s*(.+?)\s*```", re.DOTALL)


def extract_json(payload: str) -> dict:
    text = (payload or "").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    fenced = _FENCE.search(text)
    if fenced:
        try:
            return json.loads(fenced.group(1))
        except json.JSONDecodeError:
            pass
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass
    raise ValueError(f"no JSON object found in model output: {text[:160]}")


def _parse_verdict(data: dict, model: str, prompt_version: str, usage: int | None) -> Verdict:
    scores = []
    for entry in data.get("categories") or []:
        if not isinstance(entry, dict):
            continue
        scores.append(
            CategoryScore(
                category=str(entry.get("category", "")),
                confidence=float(entry.get("confidence") or 0.0),
                rationale=str(entry.get("rationale", ""))[:400],
            )
        )
    return Verdict(
        categories=scores,
        severity=(data.get("severity") or "").lower() or None,
        summary=data.get("summary"),
        model=model,
        prompt_version=prompt_version,
        token_usage=usage,
    ).normalized()


class LLMClassifier:
    def __init__(self, base_url: str, api_key: str, model: str, prompt_version: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.prompt_version = prompt_version
        self._client = httpx.Client(timeout=90)

    def classify(
        self, title: str, text: str, company_hints: list[str], source_kind: str = "rss"
    ) -> Verdict:
        settings = get_settings()
        messages = build_messages(title, text, company_hints, settings.llm_max_chars)
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        try:
            response = self._client.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json={
                    "model": self.model,
                    "messages": messages,
                    "temperature": settings.llm_temperature,
                    "response_format": {"type": "json_object"},
                },
            )
            response.raise_for_status()
            body = response.json()
            content = body["choices"][0]["message"]["content"]
            usage = (body.get("usage") or {}).get("total_tokens")
            return _parse_verdict(extract_json(content), self.model, self.prompt_version, usage)
        except Exception as exc:  # noqa: BLE001 - degrade to rules, never lose the article
            log.warning("llm failed (%s); falling back to rules", exc)
            verdict = rules_classify(
                title, text, source_kind, model=f"fallback-rules:{type(exc).__name__}",
                prompt_version=self.prompt_version,
            )
            verdict.error = str(exc)[:500]
            return verdict


def build_classifier():
    """Factory: `rules` needs no network, everything else goes through the LLM."""
    settings = get_settings()
    if settings.llm_provider == "rules":
        return None
    return LLMClassifier(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        prompt_version=settings.classify_prompt_version,
    )
