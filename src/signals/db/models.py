"""ORM models for the Automotive Signals monitoring system.

Portable across SQLite (local dev) and PostgreSQL (deployment): only types and
expressions supported by both dialects are used, which is why category filtering
relies on the normalised `event_labels` table instead of JSON operators.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

CATEGORIES = ("risk", "technology_innovation", "compliance_regulation", "investments")
COMPANY_TYPES = ("oem", "tier1")
SOURCE_KINDS = ("news_api", "rss", "press_page", "regulator")
RUN_STATUSES = ("running", "success", "partial", "failed")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UTCDateTime(TypeDecorator):
    """Every timestamp is UTC in and out, on every dialect.

    PostgreSQL `timestamptz` returns aware datetimes while SQLite returns naive
    ones, so the spec's "store it with timestamps" requirement is enforced here
    instead of at each call site.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def process_result_value(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class Base(DeclarativeBase):
    pass


class Company(Base):
    """A monitored organisation: commercial-vehicle OEM or Tier-1 supplier."""

    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    short_name: Mapped[str] = mapped_column(String(80))
    type: Mapped[str] = mapped_column(String(20))
    country: Mapped[str | None] = mapped_column(String(80))
    group: Mapped[str | None] = mapped_column(String(120))
    aliases: Mapped[list] = mapped_column(JSON, default=list)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)

    sources: Mapped[list["Source"]] = relationship(back_populates="company")
    mentions: Mapped[list["CompanyMention"]] = relationship(back_populates="company")

    @property
    def match_terms(self) -> list[str]:
        """Aliases for the entity tagger, longest first, original casing kept.

        Casing matters: the tagger matches short all-caps names such as "MAN" or
        "ZF" case-sensitively so they cannot fire inside ordinary words.
        """
        terms: dict[str, str] = {}
        for term in (self.name, self.short_name, *(self.aliases or [])):
            cleaned = (term or "").strip()
            if cleaned:
                terms.setdefault(cleaned.lower(), cleaned)
        return sorted(terms.values(), key=len, reverse=True)


class SupplyLink(Base):
    """Week-1 deliverable: which Tier-1 vendor supplies which OEM."""

    __tablename__ = "supply_links"
    __table_args__ = (UniqueConstraint("oem_id", "tier1_id", name="uq_supply_pair"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    oem_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    tier1_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    segment: Mapped[str | None] = mapped_column(String(120))
    oem: Mapped[Company] = relationship(foreign_keys=[oem_id])
    tier1: Mapped[Company] = relationship(foreign_keys=[tier1_id])


class Source(Base):
    """A location we poll for signals about a company (or the whole industry)."""

    __tablename__ = "sources"
    __table_args__ = (UniqueConstraint("url", "kind", name="uq_source_url_kind"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int | None] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(20), default="rss")
    parser: Mapped[str] = mapped_column(String(30), default="rss")
    name: Mapped[str] = mapped_column(String(200))
    url: Mapped[str] = mapped_column(String(600))
    query: Mapped[str | None] = mapped_column(String(400))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_success_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_error: Mapped[str | None] = mapped_column(Text)
    company: Mapped[Company | None] = relationship(back_populates="sources")
    articles: Mapped[list["Article"]] = relationship(back_populates="source")


class Article(Base):
    """A normalised, de-duplicated document with UTC timestamps."""

    __tablename__ = "articles"
    __table_args__ = (
        UniqueConstraint("url_hash", name="uq_article_url_hash"),
        Index("ix_articles_published_at", "published_at"),
        Index("ix_articles_content_hash", "content_hash"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"), index=True)
    url: Mapped[str] = mapped_column(String(1000))
    url_hash: Mapped[str] = mapped_column(String(64))
    content_hash: Mapped[str | None] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(600))
    summary: Mapped[str | None] = mapped_column(Text)
    clean_text: Mapped[str | None] = mapped_column(Text)
    lang: Mapped[str] = mapped_column(String(10), default="en")
    word_count: Mapped[int] = mapped_column(Integer, default=0)
    published_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    fetched_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    classified_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    needs_enrichment: Mapped[bool] = mapped_column(Boolean, default=False)

    source: Mapped[Source] = relationship(back_populates="articles")
    mentions: Mapped[list["CompanyMention"]] = relationship(back_populates="article")
    classifications: Mapped[list["Classification"]] = relationship(back_populates="article")

    @property
    def text_for_analysis(self) -> str:
        return self.clean_text or self.summary or self.title or ""


class CompanyMention(Base):
    """Entity tag produced by the NER/alias pass: article <-> company."""

    __tablename__ = "company_mentions"
    __table_args__ = (UniqueConstraint("article_id", "company_id", name="uq_mention_pair"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("articles.id", ondelete="CASCADE"), index=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    method: Mapped[str] = mapped_column(String(30), default="alias")
    mention_count: Mapped[int] = mapped_column(Integer, default=1)
    article: Mapped[Article] = relationship(back_populates="mentions")
    company: Mapped[Company] = relationship(back_populates="mentions")


class Classification(Base):
    """One LLM/rule verdict for an article, versioned by prompt + model."""

    __tablename__ = "classifications"
    __table_args__ = (
        UniqueConstraint("article_id", "prompt_version", "model", name="uq_classif_version"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("articles.id", ondelete="CASCADE"), index=True)
    model: Mapped[str] = mapped_column(String(120))
    prompt_version: Mapped[str] = mapped_column(String(20))
    categories: Mapped[list] = mapped_column(JSON, default=list)
    primary_category: Mapped[str | None] = mapped_column(String(40))
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    severity: Mapped[str | None] = mapped_column(String(20))
    rationale: Mapped[str | None] = mapped_column(Text)
    token_usage: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)

    article: Mapped[Article] = relationship(back_populates="classifications")
    labels: Mapped[list["EventLabel"]] = relationship(
        back_populates="classification", cascade="all, delete-orphan"
    )


class EventLabel(Base):
    """Exploded label rows so category filters are plain indexed SQL."""

    __tablename__ = "event_labels"
    __table_args__ = (Index("ix_event_labels_category", "category"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    classification_id: Mapped[int] = mapped_column(
        ForeignKey("classifications.id", ondelete="CASCADE"), index=True
    )
    article_id: Mapped[int] = mapped_column(ForeignKey("articles.id", ondelete="CASCADE"), index=True)
    category: Mapped[str] = mapped_column(String(40))
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    classification: Mapped[Classification] = relationship(back_populates="labels")


class IngestRun(Base):
    """Run ledger: makes the scheduler restart-safe and gives the audit trail."""

    __tablename__ = "ingest_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(20), default=RUN_STATUSES[0])
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    items_seen: Mapped[int] = mapped_column(Integer, default=0)
    items_new: Mapped[int] = mapped_column(Integer, default=0)
    detail: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)


class ValidationLabel(Base):
    """Hand-labelled gold set used to measure classifier accuracy."""

    __tablename__ = "validation_labels"
    __table_args__ = (UniqueConstraint("article_id", name="uq_validation_article"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("articles.id", ondelete="CASCADE"), index=True)
    categories: Mapped[list] = mapped_column(JSON, default=list)
    labeler: Mapped[str] = mapped_column(String(80), default="manual")
    labelled_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
