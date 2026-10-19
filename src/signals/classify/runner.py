"""Classification runner: articles in, versioned verdicts out."""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from signals.classify.provider import build_classifier
from signals.classify.rules import classify as rules_classify
from signals.classify.schema import Verdict
from signals.config import get_settings
from signals.db.models import (
    Article,
    CATEGORIES,
    Classification,
    Company,
    CompanyMention,
    EventLabel,
    IngestRun,
    utcnow,
)
from signals.logging_setup import get_logger

log = get_logger(__name__)
RULES_MODEL = "rules-lex-v3"


def _pending_article_ids(session: Session, model: str, prompt_version: str, limit: int) -> list[int]:
    classified = (
        select(Classification.article_id)
        .where(Classification.model == model, Classification.prompt_version == prompt_version)
    )
    rows = session.scalars(
        select(Article.id)
        .where(Article.id.not_in(classified))
        .order_by(Article.published_at.desc(), Article.id.desc())
        .limit(limit)
    )
    return list(rows)


def _hints_and_kind(session: Session, article_id: int) -> tuple[str, list[str]]:
    article = session.get(Article, article_id)
    mentioned_ids = list(
        session.scalars(
            select(CompanyMention.company_id).where(CompanyMention.article_id == article_id)
        )
    )
    names: list[str] = []
    if mentioned_ids:
        names = list(
            session.scalars(select(Company.name).where(Company.id.in_(mentioned_ids)))
        )
    source_kind = article.source.kind if article and article.source else "rss"
    return source_kind, names


def persist(session: Session, article_id: int, verdict: Verdict) -> Classification:
    row = Classification(
        article_id=article_id,
        model=verdict.model,
        prompt_version=verdict.prompt_version,
        categories=verdict.to_json(),
        primary_category=verdict.primary,
        confidence=verdict.confidence,
        severity=verdict.severity,
        rationale="; ".join(s.rationale for s in verdict.categories if s.rationale)[:2000] or None,
        token_usage=verdict.token_usage,
        error=verdict.error,
    )
    session.add(row)
    session.flush()
    article = session.get(Article, article_id)
    if article:
        article.classified_at = utcnow()
    session.flush()
    return row


def refresh_event_labels(session: Session) -> int:
    """Rebuild `event_labels` from the current best verdict per article.

    `classifications` is append-only so prompt/model versions stay comparable;
    the flat label index is the queryable view of the *current* verdict, so it is
    derived here instead of accumulating one set of rows per version.
    """
    session.execute(delete(EventLabel))
    ranked = session.scalars(
        select(Classification).order_by(
            Classification.article_id, Classification.confidence.desc(), Classification.id.desc()
        )
    )
    seen: set[int] = set()
    stored = 0
    for row in ranked:
        if row.article_id in seen:
            continue
        seen.add(row.article_id)
        for entry in row.categories or []:
            category = entry.get("category")
            if category not in CATEGORIES:
                continue
            session.add(
                EventLabel(
                    classification_id=row.id,
                    article_id=row.article_id,
                    category=category,
                    confidence=float(entry.get("confidence") or row.confidence or 0.0),
                )
            )
            stored += 1
    session.flush()
    return stored


def run_classification(session: Session, limit: int | None = None, batch_size: int | None = None) -> IngestRun:
    settings = get_settings()
    prompt_version = settings.classify_prompt_version
    llm = build_classifier()
    model = llm.model if llm else RULES_MODEL
    run = IngestRun(kind="classify")
    session.add(run)
    session.flush()

    total = 0
    done = 0
    ids = _pending_article_ids(session, model, prompt_version, limit or 500)
    for article_id in ids:
        article = session.get(Article, article_id)
        if article is None:
            continue
        source_kind, names = _hints_and_kind(session, article_id)
        text = article.text_for_analysis
        if llm is not None:
            verdict = llm.classify(article.title, text, names, source_kind)
        else:
            verdict = rules_classify(
                article.title, text, source_kind, model=RULES_MODEL, prompt_version=prompt_version
            )
        persist(session, article_id, verdict)
        total += 1
        if verdict.categories:
            done += 1
        if total % (batch_size or settings.classify_batch_size) == 0:
            session.flush()
            log.info("classified %d/%d", total, len(ids))

    run.items_seen = total
    run.items_new = done
    run.finished_at = utcnow()
    run.status = "success" if total else "failed"
    labels = refresh_event_labels(session)
    run.detail = f"{done}/{total} labelled, {labels} index rows, model={model}, prompt={prompt_version}"
    session.flush()
    log.info("classification run %s %s", run.id, run.detail)
    return run
