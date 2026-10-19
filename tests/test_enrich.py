"""Newsroom sources: index page -> body fetch -> the date the page declares.

Ingesting a press-release index yields titles only. Without the enrichment pass
those rows are one sentence long, dated "today", and classified from a headline -
which is exactly how a monitoring system invents recent events.
"""

from __future__ import annotations

import httpx
import respx
from sqlalchemy import func, select

from signals.db.models import Article, CompanyMention, Source, utcnow
from signals.etl.runner import enrich_bodies, run_etl
from signals.ingest.parsers import parse_press_page
from signals.ingest.runner import run_ingestion

INDEX = b"""<html><body>
  <div class="release-list">
    <a href="/press/2026/hydrogen-fuel-cell-racks.html">
      Serial production of hydrogen fuel cell racks starts this spring</a>
    <a href="/press/2025/recall-steering-columns.html">
      ZF recalls electric power steering columns for light commercial vehicles</a>
  </div>
</body></html>"""

RELEASE = b"""<html><head>
  <meta property="article:published_time" content="2026-03-04T06:00:00Z"/>
</head><body><article>
  <h1>Serial production of hydrogen fuel cell racks starts this spring</h1>
  <p>The Friedrichshafen line builds fuel cell racks for commercial vehicles.
  Serial production starts with 300 units a year and ZF plans to add a second
  shift once further contracts are signed. Daimler Truck is the launch customer
  and the investment totalled EUR 40 million, creating 120 jobs at the site.</p>
</article></body></html>"""


def test_relative_newsroom_links_are_resolved_against_the_index_page():
    items = parse_press_page(
        INDEX, limit=5, item_selector=".release-list a", base_url="https://press.zf.com/press/en/releases.html"
    )
    assert len(items) == 2
    assert items[0].url == "https://press.zf.com/press/2026/hydrogen-fuel-cell-racks.html"
    assert items[0].published_at is None, "the index page carries no date"


@respx.mock
def test_enrichment_fetches_body_and_restores_the_declared_date(session, watchlist):
    respx.get("https://newsroom.example.test/releases.html").mock(
        return_value=httpx.Response(200, content=INDEX, headers={"content-type": "text/html"})
    )
    body_route = respx.get("https://newsroom.example.test/press/2026/hydrogen-fuel-cell-racks.html").mock(
        return_value=httpx.Response(200, content=RELEASE, headers={"content-type": "text/html"})
    )
    source = Source(
        company_id=watchlist["ZF Friedrichshafen AG"].id, name="example newsroom",
        kind="press_page", parser="press_page", url="https://newsroom.example.test/releases.html",
        query=".release-list a", enabled=True,
    )
    session.add(source)
    session.flush()

    run_ingestion(session, source_ids=[source.id], limit=10)
    headline_only = list(
        session.scalars(select(Article).where(Article.needs_enrichment.is_(True)).order_by(Article.id))
    )
    assert len(headline_only) == 2
    assert all(len((a.clean_text or "").split()) < 20 for a in headline_only)
    assert headline_only[0].published_at == headline_only[0].fetched_at, "no date available yet"

    # First ETL pass with enrichment switched off: the headline names nobody, so
    # the article is tagged empty and would never be revisited unless
    # enrichment reopens it.
    run_etl(session, enrich=0)
    assert session.scalar(
        select(func.count(CompanyMention.id)).where(CompanyMention.article_id == headline_only[0].id)
    ) == 0

    run_etl(session)
    assert body_route.calls.call_count == 1

    article = session.get(Article, headline_only[0].id)
    assert article.needs_enrichment is False
    assert "fuel cell" in article.clean_text
    assert article.word_count > 30
    assert (article.published_at.year, article.published_at.month) == (2026, 3), (
        "the page declares its own date - the trend chart must not show today"
    )
    tagged = {
        m.company_id: m.mention_count
        for m in session.scalars(select(CompanyMention).where(CompanyMention.article_id == article.id))
    }
    assert {watchlist["ZF Friedrichshafen AG"].id, watchlist["Daimler Truck Holding AG"].id} <= set(tagged), (
        "companies that only appear in the body must still be tagged"
    )


@respx.mock
def test_undated_or_thin_pages_keep_their_flag_for_the_next_pass(session, watchlist):
    respx.get("https://newsroom.example.test/b").mock(return_value=httpx.Response(403))
    respx.get("https://newsroom.example.test/c").mock(
        return_value=httpx.Response(200, content=b"<html><p>enable javascript</p></html>",
                                   headers={"content-type": "text/html"})
    )
    now = utcnow()
    source = Source(name="example newsroom", kind="press_page", parser="press_page",
                    url="https://newsroom.example.test/releases.html", enabled=True)
    session.add(source)
    session.flush()
    for slug in ("b", "c"):
        session.add(Article(
            source_id=source.id, url=f"https://newsroom.example.test/{slug}",
            url_hash=slug, title=f"release {slug} carrying a long enough headline",
            clean_text="headline only", summary="headline only", word_count=2, lang="en",
            published_at=now, fetched_at=now, needs_enrichment=True,
        ))
    session.flush()

    result = enrich_bodies(session, limit=10)
    assert result["enriched"] == 0
    assert result["failed"] == 2

    pending = list(session.scalars(select(Article).where(Article.needs_enrichment.is_(True))))
    assert len(pending) == 2, "a 403 and a JS shell must be retried, not marked done"
    assert all(a.word_count == 2 for a in pending), "a thin page must not overwrite the headline"
