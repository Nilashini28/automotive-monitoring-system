"""Classifier result contract shared by the LLM and rule backends."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from signals.classify.prompts import CATEGORY_LABELS
from signals.db.models import CATEGORIES

# Models echo the display labels, drop words, or use singular forms. Everything is
# collapsed to separators first, then mapped onto the canonical category key.
_CATEGORY_ALIASES = {
    "risk": "risk",
    "technology": "technology_innovation",
    "innovation": "technology_innovation",
    "technology_innovation": "technology_innovation",
    "tech_innovation": "technology_innovation",
    "technology_and_innovation": "technology_innovation",
    "regulation": "compliance_regulation",
    "compliance": "compliance_regulation",
    "regulatory": "compliance_regulation",
    "compliance_regulation": "compliance_regulation",
    "compliance_and_regulation": "compliance_regulation",
    "investment": "investments",
    "investing": "investments",
    "investments": "investments",
}


def canonical_category(raw: str | None) -> str | None:
    key = re.sub(r"[^a-z0-9]+", "_", (raw or "").lower()).strip("_")
    return _CATEGORY_ALIASES.get(key)


@dataclass
class CategoryScore:
    category: str
    confidence: float
    rationale: str = ""


@dataclass
class Verdict:
    categories: list[CategoryScore] = field(default_factory=list)
    severity: str | None = None
    summary: str | None = None
    model: str = "unknown"
    prompt_version: str = "v1"
    token_usage: int | None = None
    error: str | None = None

    def normalized(self) -> "Verdict":
        valid = set(CATEGORIES)
        best: dict[str, CategoryScore] = {}
        for score in self.categories:
            key = canonical_category(score.category)
            if key not in valid:
                continue
            score.category = key
            score.confidence = max(0.0, min(1.0, float(score.confidence or 0.0)))
            if score.confidence < 0.2:
                continue
            previous = best.get(key)
            if previous is None or score.confidence > previous.confidence:
                best[key] = score
        kept = list(best.values())
        kept.sort(key=lambda s: s.confidence, reverse=True)
        self.categories = kept
        severity = (self.severity or "").strip().lower()
        self.severity = severity if severity in ("low", "medium", "high") else None
        return self

    @property
    def primary(self) -> str | None:
        return self.categories[0].category if self.categories else None

    @property
    def confidence(self) -> float:
        return round(self.categories[0].confidence, 3) if self.categories else 0.0

    def to_json(self) -> list[dict]:
        return [
            {
                "category": s.category,
                "label": CATEGORY_LABELS.get(s.category, s.category),
                "confidence": round(s.confidence, 3),
                "rationale": s.rationale,
            }
            for s in self.categories
        ]

    def highlight(self) -> bool:
        """A signal worth surfacing at the top of the dashboard."""
        return bool(self.categories) and (
            self.severity == "high" or self.categories[0].confidence >= 0.85
        )
