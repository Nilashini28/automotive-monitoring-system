"""LLM provider: JSON extraction, verdict parsing and the offline fallback."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from signals.classify.provider import LLMClassifier, _parse_verdict, extract_json
from signals.classify.prompts import FEW_SHOT, SYSTEM_PROMPT, build_messages


def test_extract_json_handles_plain_fenced_and_embedded():
    assert extract_json('{"categories": []}')["categories"] == []
    assert extract_json("```json\n{\"a\": 1}\n```")["a"] == 1
    assert extract_json("Here you go: {\"a\": 2} thanks")["a"] == 2
    with pytest.raises(ValueError):
        extract_json("no json at all")


def test_parse_verdict_normalises_aliases_and_drops_low_confidence():
    verdict = _parse_verdict(
        {
            "categories": [
                {"category": "Technology & Innovation", "confidence": 0.9, "rationale": "x"},
                {"category": "regulation", "confidence": 0.7, "rationale": "y"},
                {"category": "risk", "confidence": 0.05, "rationale": "weak"},
                {"category": "sports", "confidence": 0.99, "rationale": "hallucinated"},
            ],
            "severity": "CRITICAL",
        },
        "gpt-test",
        "v1",
        10,
    ).normalized()
    cats = {c.category for c in verdict.categories}
    assert cats == {"technology_innovation", "compliance_regulation"}
    assert verdict.severity is None  # 'CRITICAL' is not in the allowed enum


def test_prompt_payload_carries_system_and_few_shot_pairs():
    messages = build_messages("Title here", "body", ["AB Volvo"], max_chars=100)
    assert messages[0]["role"] == "system"
    assert "commercial-vehicle" in SYSTEM_PROMPT
    assert len(messages) == 1 + 2 * len(FEW_SHOT) + 1
    assert "AB Volvo" in messages[-1]["content"]


@respx.mock
def test_classifier_calls_openai_compatible_endpoint_and_stores_usage():
    route = respx.post("https://llm.test/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": json.dumps(
                    {"categories": [{"category": "risk", "confidence": 0.91, "rationale": "recall"}],
                     "severity": "high", "summary": "s"}
                )}}],
                "usage": {"total_tokens": 1234},
            },
        )
    )
    clf = LLMClassifier("https://llm.test/v1", "k", "gpt-test", "v1")
    verdict = clf.classify("Recall announced", "body text", ["PACCAR"])
    assert route.called
    assert verdict.primary == "risk"
    assert verdict.confidence == 0.91
    assert verdict.token_usage == 1234
    assert verdict.error is None


@respx.mock
def test_http_failure_degrades_to_rules_instead_of_losing_the_article():
    respx.post("https://llm.test/v1/chat/completions").mock(return_value=httpx.Response(500))
    clf = LLMClassifier("https://llm.test/v1", "k", "gpt-test", "v1")
    verdict = clf.classify("Paccar recalls trucks over brake defect", "investigation open", [])
    assert verdict.error
    assert verdict.model.startswith("fallback-rules")
    assert verdict.primary == "risk"


@respx.mock
def test_malformed_model_output_also_falls_back():
    respx.post("https://llm.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "sorry!"}}]})
    )
    clf = LLMClassifier("https://llm.test/v1", "k", "gpt-test", "v1")
    verdict = clf.classify("New hydrogen platform unveiled", "", [])
    assert verdict.error
    assert verdict.primary == "technology_innovation"
