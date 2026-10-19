"""Ingestion runner: fetch every enabled source, dedupe, persist, record the run."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from signals.config import get_settings
from signals.db.models import Article, IngestRun, Source, utcnow
from signals.ingest.clean import canonical_url, url_hash
from signals.ingest.dedupe import Candidate, filter_new
from signals.ingest.fetch import build_client, fetch
from signals.ingest.parsers import PARSERS, RawItem
from signals.logging_setup import get_logger

log = get_logger(__name__)

# Feed templates and epoch-dated entries are the realistic garbage here. A
# genuine 2010 press release that Google Search still surfaces must keep its
# own date - rewriting it to "today" would invent events in the trend chart.
MIN_PLAUSIBLE = datetime(1990, 1, 1, tzinfo=timezone.utc)
_SKEW = timedelta(hours=6)
SEC_LOOKBACK_DAYS = 180


def _request_url(source: Source) -> str:
    """EDGAR full-text search ranks by relevance over all time, so without a
    window it returns filings from 2001. Recent filings are the signal."""
    if source.parser != "sec_edgar" or "startdt" in source.url:
        return source.url
    end = utcnow().date()
    start = end - timedelta(days=SEC_LOOKBACK_DAYS)
    separator = "&" if "?" in source.url else "?"
    return f"{source.url}{separator}dateRange=custom&startdt={start}&enddt={end}"


def _candidate(item: RawItem) -> Candidate:
    return Candidate(
        url_hash=url_hash(item.url),
        title=item.title,
        body=item.body or item.summary,
        item=item,
    )


def _to_article(item: RawItem, source_id: int) -> Article:
    body = item.body or item.summary or item.title
    now = utcnow()
    published = item.published_at or now
    # Feeds occasionally carry template dates (2001-01-01) or a wrong year;
    # trusting them would poison the trend chart and the date filters.
    if published < MIN_PLAUSIBLE or published > now + _SKEW:
        published = now
    return Article(
        source_id=source_id,
        url=canonical_url(item.url)[:1000],
        url_hash=url_hash(item.url),
        title=item.title[:600],
        summary=(item.summary or "")[:4000] or None,
        clean_text=body[:20000] or None,
        lang=(item.lang or "en")[:10],
        word_count=len(body.split()),
        published_at=published,
        fetched_at=now,
        needs_enrichment=bool(item.meta.get("needs_body")),
    )


def collect_source(
    client: httpx.Client, session: Session, source: Source, limit: int | None = None
) -> tuple[int, int]:
    settings = get_settings()
    parser = PARSERS.get(source.parser)
    if parser is None:
        raise ValueError(f"no parser registered for {source.parser}")
    try:
        response = fetch(client, _request_url(source))
        if response.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {response.status_code}", request=response.request, response=response
            )
        items = parser(
            response.content,
            limit or settings.ingest_max_articles_per_source,
            source.query,
            source.url,
        )
    except Exception as exc:  # noqa: BLE001 - a dead source must not kill the job
        source.last_error = f"{type(exc).__name__}: {exc}"[:1000]
        log.warning("source failed id=%s name=%s error=%s", source.id, source.name, exc)
        return 0, 0

    fresh = filter_new(session, [_candidate(i) for i in items if i.url])
    for candidate in fresh:
        session.add(_to_article(candidate.item, source.id))
    source.last_error = None
    source.last_success_at = utcnow()
    session.flush()
    log.info("source ok id=%s name=%s seen=%d new=%d", source.id, source.name, len(items), len(fresh))
    return len(items), len(fresh)


def run_ingestion(
    session: Session, source_ids: list[int] | None = None, limit: int | None = None
) -> IngestRun:
    run = IngestRun(kind="ingest")
    session.add(run)
    session.flush()

    query = select(Source).where(Source.enabled.is_(True)).order_by(Source.id)
    if source_ids:
        query = query.where(Source.id.in_(source_ids))
    sources = list(session.scalars(query))

    seen = new = 0
    failures: list[str] = []
    with build_client() as client:
        for source in sources:
            try:
                source_seen, source_new = collect_source(client, session, source, limit)
                seen += source_seen
                new += source_new
                if source.last_error:
                    failures.append(f"{source.name}: {source.last_error}")
            except Exception as exc:  # noqa: BLE001
                failures.append(f"{source.name}: {exc}")
                log.exception("unexpected failure on source %s", source.name)
    session.flush()

    run.items_seen = seen
    run.items_new = new
    run.finished_at = utcnow()
    run.status = "success" if not failures else ("partial" if new else "failed")
    run.detail = f"{new} new articles from {len(sources)} sources"
    run.error = " | ".join(failures)[:2000] if failures else None
    session.flush()
    log.info("ingestion run %s status=%s seen=%s new=%s", run.id, run.status, seen, new)
    return run
