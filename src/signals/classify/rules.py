"""Offline rule classifier.

Runs with no API key and no GPU, which keeps the deployed prototype cheap and
gives the LLM path a measurable baseline. Confidence is a lexical-density score,
not a probability, so it is comparable run to run but must not be read as
calibrated.
"""

from __future__ import annotations

import re

from signals.classify.schema import CategoryScore, Verdict

_LEXICON: dict[str, tuple[float, tuple[str, ...]]] = {
    "risk": (
        0.35,
        (
            "recall", "defect", "investigation", "nhtsa", "lawsuit", "litigation",
            "sanction", "tariff", "layoff", "shutdown", "halt", "shortage", "cyber",
            "breach", "warranty", "fine", "penalty", "crash", "fire risk", "stop sale",
            "bankruptcy", "insolvency", "slowdown", "decline", "loss", "fall", "drop",
            "settlement", "fraud", "embezzle", "theft", "contamination", "outage",
            "strike", "downgrade", "supply chain disruption", "supply chain crisis",
        ),
    ),
    "technology_innovation": (
        0.35,
        (
            "launch", "unveil", "new platform", "electric", "ev", "hydrogen", "fuel cell",
            "autonomous", "self-driving", "driverless", "adas", "driver assist", "software",
            "over-the-air", "patent", "prototype", "pilot", "test programme", "connected",
            "telematics", "battery", "e-axle", "innovation", "digital twin", "develop",
        ),
    ),
    "compliance_regulation": (
        0.4,
        (
            "regulation", "regulatory", "compliance", "type approval", "homologation",
            "euro 7", "euro vii", "co2 standard", "emission", "directive", "mandate",
            "approval", "standard", "legislation", "law", "ruling", "tariff",
            "reporting requirement", "8-k", "10-k", "10-q", "filing", "sec filing",
            "eu commission", "parliament", "enforcement", "due diligence", "csrd",
        ),
    ),
    "investments": (
        0.4,
        (
            "invest", "capex", "new plant", "factory", "facility", "expansion", "expand",
            "joint venture", "acquisition", "acquire", "stake", "fund", "financing",
            "contract awarded", "supply agreement", "buyback", "repurchase", "dividend",
            "capacity", "production start", "greenfield", "shareholder", "manufacturing site",
            "jobs",
        ),
    ),
}


def _term_pattern(term: str) -> re.Pattern[str]:
    """Whole-word match with light inflection.

    Word boundaries matter: a raw substring test made "sec" fire inside
    "second-quarter" and invented regulation news. Single words also accept
    plural/verb endings so "recall" catches "recalls" and "tariff" catches
    "tariffs"; short acronyms (ev, sec) stay exact.
    """
    cleaned = term.strip()
    word = re.escape(cleaned)
    if " " not in cleaned and len(cleaned) >= 3:
        word = rf"{word}(?:s|es|ed|d|ing)?"
    return re.compile(rf"(?<![\w-]){word}(?![\w-])")


_COMPILED: dict[str, tuple[float, list[tuple[str, re.Pattern[str]]]]] = {
    category: (base, [(term, _term_pattern(term)) for term in terms])
    for category, (base, terms) in _LEXICON.items()
}


def _score_category(hay: str, category: str) -> tuple[int, list[str]]:
    hits = 0
    matched: list[str] = []
    for term, pattern in _COMPILED[category][1]:
        if pattern.search(hay):
            hits += 1
            matched.append(term)
    return hits, matched


def classify(title: str, text: str, source_kind: str = "rss", model: str = "rules", prompt_version: str = "v1") -> Verdict:
    hay = f"{title} {text}".lower()
    scores: list[CategoryScore] = []
    for category, (base, _terms) in _COMPILED.items():
        hits, matched = _score_category(hay, category)
        if source_kind in ("regulator", "press_page") and category == "compliance_regulation":
            hits += 1
            matched.append(f"{source_kind} source")
        if hits == 0:
            continue
        confidence = min(0.9, base + 0.13 * hits)
        rationale = "rule match: " + ", ".join(matched[:3])
        scores.append(CategoryScore(category=category, confidence=confidence, rationale=rationale))

    verdict = Verdict(categories=scores, model=model, prompt_version=prompt_version).normalized()
    if verdict.categories:
        top = verdict.categories[0].confidence
        verdict.severity = "high" if top >= 0.75 else ("medium" if top >= 0.55 else "low")
        verdict.summary = (title or "")[:180]
    return verdict
