"""End-to-end: mocked feed -> ingest -> ETL -> classify -> API response.

This is the test that proves the pieces are wired together, using a captured RSS
document instead of the live network so it stays deterministic.
"""

from __future__ import annotations

import httpx
import respx
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from signals.api.app import app
from signals.classify.runner import run_classification
from signals.db.models import Article, Classification, CompanyMention, EventLabel, Source
from signals.etl.runner import run_etl
from signals.ingest.runner import run_ingestion

RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>Volvo Group news</title>
  <item><title>Volvo Group recalls 4,200 trucks over brake defect</title>
    <link>https://news.example/volvo-recall</link>
    <pubDate>Mon, 12 Oct 2026 07:00:00 GMT</pubDate>
    <description>NHTSA opened an investigation into the braking system.</description></item>
  <item><title>Volvo Group recalls 4,200 trucks over brake defect</title>
    <link>https://news.example/volvo-recall?utm_source=feed</link>
    <pubDate>Mon, 12 Oct 2026 07:05:00 GMT</pubDate>
    <description>Same wire copy, syndicated.</description></item>
  <item><title>Volvo Group opens hydrogen assembly line in Ghent</title>
    <link>https://news.example/volvo-hydrogen</link>
    <pubDate>Tue, 13 Oct 2026 07:00:00 GMT</pubDate>
    <description>The plant investment adds 600 jobs.</description></item>
</channel></rss>"""


@respx.mock
def test_full_pipeline_from_feed_to_api(session, watchlist):
    respx.get("https://news.example/rss").mock(
        return_value=httpx.Response(200, content=RSS, headers={"content-type": "application/rss+xml"})
    )
    source = Source(
        company_id=watchlist["AB Volvo"].id, name="mock feed", kind="rss",
        parser="google_news", url="https://news.example/rss", query="volvo", enabled=True,
    )
    session.add(source)
    session.commit()

    run = run_ingestion(session, source_ids=[source.id], limit=10)
    assert run.status == "success"
    assert run.items_seen == 3
    assert run.items_new == 2, "the utm-only duplicate must be collapsed"

    assert run_etl(session)["articles"] == 2
    volvo_mentions = session.scalars(
        select(CompanyMention).where(CompanyMention.company_id == watchlist["AB Volvo"].id)
    )
    assert len(list(volvo_mentions)) == 2

    classify_run = run_classification(session)
    assert classify_run.status == "success"
    assert session.scalar(select(func.count(Classification.id))) == 2

    labels = {row[0] for row in session.execute(select(EventLabel.category)).all()}
    assert "risk" in labels

    # The stages only flush - the caller owns the transaction, exactly as the CLI
    # and worker do through session_scope(). Commit before the API reads it back
    # on its own connection.
    session.commit()
    with TestClient(app) as client:
        body = client.get("/events?category=risk").json()
        assert body["total"] == 1
        item = body["items"][0]
        assert "recalls" in item["title"]
        assert item["companies"] == ["Volvo Group"]
        assert item["source_kind"] == "rss"
        assert item["published_at"].startswith("2026-10-12")
        assert client.get("/search?q=hydrogen").json()["total"] == 1


@respx.mock
def test_a_dead_source_is_recorded_not_fatal(session, watchlist):
    respx.get("https://dead.example/rss").mock(return_value=httpx.Response(503))
    respx.get("https://alive.example/rss").mock(
        return_value=httpx.Response(200, content=RSS, headers={"content-type": "application/rss+xml"})
    )
    dead = Source(name="dead", kind="rss", parser="rss", url="https://dead.example/rss", enabled=True)
    alive = Source(name="alive", kind="rss", parser="rss", url="https://alive.example/rss", enabled=True)
    session.add_all([dead, alive])
    session.flush()

    run = run_ingestion(session, source_ids=[dead.id, alive.id], limit=10)
    assert run.items_new == 2, "the healthy source must still deliver"
    assert "503" in dead.last_error
    assert run.status == "partial"
    assert alive.last_error is None
    assert alive.last_success_at is not None


def test_timestamp_policy_keeps_old_but_invents_nothing():
    from datetime import datetime, timedelta, timezone

    from signals.ingest.parsers import RawItem
    from signals.ingest.runner import _to_article

    def item(**kw):
        return _to_article(RawItem(url="https://a.test/1", title="t", body="b", **kw), source_id=1)

    now = datetime.now(timezone.utc)
    assert item(published_at=datetime(2010, 6, 16, tzinfo=timezone.utc)).published_at.year == 2010, (
        "a real 2010 article must not become today's news"
    )
    assert item(published_at=datetime(1970, 1, 1, tzinfo=timezone.utc)).published_at >= now - timedelta(days=1)
    assert item(published_at=now + timedelta(days=200)).published_at <= now + timedelta(hours=1)


def test_ingestion_is_idempotent_across_runs(session, watchlist):
    @respx.mock
    def inner():
        respx.get("https://news.example/rss").mock(
            return_value=httpx.Response(200, content=RSS, headers={"content-type": "application/rss+xml"})
        )
        source = Source(name="mock", kind="rss", parser="rss", url="https://news.example/rss", enabled=True)
        session.add(source)
        session.flush()
        first = run_ingestion(session, source_ids=[source.id], limit=10)
        second = run_ingestion(session, source_ids=[source.id], limit=10)
        assert first.items_new == 2
        assert second.items_new == 0
        assert session.scalar(select(func.count(Article.id))) == 2

    inner()
