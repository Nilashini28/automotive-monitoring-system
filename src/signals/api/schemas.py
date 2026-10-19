"""Response models + serializers. Keeping the wire format explicit means the
Streamlit client and any future consumer never depend on ORM attribute names."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from signals.api.queries import EventRow
from signals.classify.prompts import CATEGORY_LABELS


class CompanyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    short_name: str
    type: str
    country: str | None = None
    group: str | None = None
    aliases: list[str] = Field(default_factory=list)


class LabelOut(BaseModel):
    category: str
    label: str
    confidence: float
    rationale: str = ""


class EventOut(BaseModel):
    id: int
    title: str
    url: str
    summary: str | None = None
    published_at: datetime
    fetched_at: datetime
    source: str
    source_kind: str
    companies: list[str] = Field(default_factory=list)
    categories: list[LabelOut] = Field(default_factory=list)
    primary_category: str | None = None
    confidence: float = 0.0
    severity: str | None = None
    highlight: bool = False
    model: str | None = None
    prompt_version: str | None = None
    word_count: int = 0


class PageOut(BaseModel):
    items: list[EventOut]
    total: int
    limit: int
    offset: int


def event_out(row: EventRow, include_rationale: bool = True) -> EventOut:
    article = row.article
    classification = row.classification
    labels = []
    if classification is not None:
        for entry in classification.categories or []:
            labels.append(
                LabelOut(
                    category=entry.get("category", ""),
                    label=CATEGORY_LABELS.get(entry.get("category", ""), entry.get("category", "")),
                    confidence=float(entry.get("confidence") or 0.0),
                    rationale=entry.get("rationale", "") if include_rationale else "",
                )
            )
    severity = classification.severity if classification else None
    confidence = classification.confidence if classification else 0.0
    return EventOut(
        id=article.id,
        title=article.title,
        url=article.url,
        summary=article.summary,
        published_at=article.published_at,
        fetched_at=article.fetched_at,
        source=article.source.name if article.source else "unknown",
        source_kind=article.source.kind if article.source else "rss",
        companies=[c.short_name for c in row.companies],
        categories=labels,
        primary_category=classification.primary_category if classification else None,
        confidence=confidence,
        severity=severity,
        highlight=(severity == "high" or confidence >= 0.85),
        model=classification.model if classification else None,
        prompt_version=classification.prompt_version if classification else None,
        word_count=article.word_count,
    )
