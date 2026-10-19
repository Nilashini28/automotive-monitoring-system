"""ETL pass: raw ingested rows to structured, entity-tagged, UTC-normalised rows."""

from __future__ import annotations

import httpx
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from signals.db.models import Article, CompanyMention, utcnow
from signals.etl.entities import load_matcher, tag_article
from signals.ingest.clean import clean_body, html_published_at, html_to_text, to_utc, truncate_summary
from signals.ingest.fetch import build_client, fetch
from signals.logging_setup import get_logger

log = get_logger(__name__)

# A page that yields less than this is a cookie wall, a JS shell, or the index
# page we already have - not a body worth storing.
MIN_BODY_CHARS = 250


def enrich_bodies(session: Session, limit: int = 40) -> dict[str, object]:
    """Fetch the full pages behind title-only sources (EDGAR, newsrooms).

    Classifying a one-line summary is guesswork, so flagged rows get their real
    text and, when the page declares one, its publication date. A failed fetch
    leaves `needs_enrichment` set so the next pass retries it.
    """
    rows = list(
        session.scalars(
            select(Article)
            .where(Article.needs_enrichment.is_(True))
            .order_by(Article.id)
            .limit(limit)
        )
    )
    if not rows:
        return {"enriched": 0, "failed": 0, "ids": []}
    ids: list[int] = []
    failed = 0
    with build_client() as client:
        for article in rows:
            try:
                response = fetch(client, article.url)
                if response.status_code >= 400:
                    raise httpx.HTTPStatusError(
                        f"HTTP {response.status_code}",
                        request=response.request,
                        response=response,
                    )
                text = clean_body(html_to_text(response.text))
                if len(text) < MIN_BODY_CHARS:
                    raise ValueError(f"only {len(text)} chars of usable text")
            except Exception as exc:  # noqa: BLE001 - one dead page must not stall the pass
                failed += 1
                log.debug("enrich failed id=%s url=%s error=%s", article.id, article.url, exc)
                continue
            article.clean_text = text[:20000]
            article.word_count = len(text.split())
            if not article.summary:
                article.summary = truncate_summary(text)
            declared = html_published_at(response.text)
            # `published_at == fetched_at` marks a row the parser could not date:
            # ingest stamps both from the same clock reading. A real parsed date wins.
            if declared is not None and article.published_at == article.fetched_at:
                article.published_at = to_utc(declared)
            article.needs_enrichment = False
            ids.append(article.id)
        session.flush()
    log.info("enrich done=%d failed=%d", len(ids), failed)
    return {"enriched": len(ids), "failed": failed, "ids": ids}


def run_etl(
    session: Session, limit: int = 2000, retag: bool = False, enrich: int = 40
) -> dict[str, object]:
    """Fetch missing bodies, clean text, normalise timestamps, tag entities.

    Returns counters for the run ledger.
    """
    enrichment = enrich_bodies(session, limit=enrich) if enrich else {"enriched": 0, "ids": []}
    enriched_ids = enrichment["ids"]
    matcher = load_matcher(session)
    query = select(Article).order_by(Article.id).limit(limit)
    if not retag:
        tagged_ids = select(CompanyMention.article_id).distinct()
        # An enriched row already has mentions from its title-only pass, and the
        # body can name companies the headline never did.
        query = (
            select(Article)
            .where(or_(Article.id.not_in(tagged_ids), Article.id.in_(enriched_ids)))
            .order_by(Article.id)
            .limit(limit)
        )

    cleaned = 0
    articles = list(session.scalars(query))
    for article in articles:
        body = article.clean_text or ""
        if body and body != clean_body(body):
            article.clean_text = clean_body(body)
            article.word_count = len(article.clean_text.split())
            cleaned += 1
        if article.published_at is None or article.published_at.tzinfo is None:
            article.published_at = to_utc(article.published_at)
        tag_article(session, article, matcher)

    log.info("etl done articles=%d cleaned=%d enriched=%d", len(articles), cleaned, len(enriched_ids))
    return {
        "articles": len(articles),
        "cleaned": cleaned,
        "enriched": enrichment["enriched"],
        "at": utcnow().isoformat(),
    }
