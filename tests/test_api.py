"""HTTP contract for the FastAPI service."""

from __future__ import annotations

from datetime import datetime, timezone


def test_health_reports_active_classifier(client):
    body = client.get("/healthz").json()
    assert body["status"] == "ok"
    assert body["provider"] == "rules"


def test_events_requires_a_classification(client, session, make_article):
    from sqlalchemy import func, select

    from signals.db.models import Article

    plain = make_article(title="Unclassified headline")
    assert session.scalar(select(func.count(Article.id))) == 1
    assert client.get("/events").json()["total"] == 0
    make_article(title="Recall announced", categories=["risk"], severity="high", confidence=0.9)
    assert client.get("/events").json()["total"] == 1
    assert plain.title == "Unclassified headline"


def test_pagination_returns_stable_disjoint_pages(client, make_article):
    for i in range(5):
        make_article(title=f"Headline {i}", body=f"body {i}", categories=["risk"])
    first = client.get("/events?limit=2&offset=0").json()
    second = client.get("/events?limit=2&offset=2").json()
    assert first["total"] == 5
    assert len(first["items"]) == 2 and len(second["items"]) == 2
    assert {i["id"] for i in first["items"]}.isdisjoint({i["id"] for i in second["items"]})


def test_category_company_severity_and_confidence_filters(client, make_article, watchlist):
    make_article(title="Volvo recall", company="AB Volvo", categories=["risk"], severity="high", confidence=0.9)
    make_article(title="Cummins plant", company="Cummins Inc.", categories=["investments"], severity="low", confidence=0.4)

    volvo_id = watchlist["AB Volvo"].id
    assert [i["primary_category"] for i in client.get("/events?category=risk").json()["items"]] == ["risk"]
    assert client.get(f"/events?company={volvo_id}").json()["total"] == 1
    assert client.get("/events?severity=high").json()["total"] == 1
    assert client.get("/events?min_confidence=0.8").json()["total"] == 1
    assert client.get("/events?highlight_only=true").json()["total"] == 1
    multi = client.get("/events?category=risk&category=investments").json()
    assert multi["total"] == 2


def test_unknown_category_and_oversized_limit_are_rejected(client):
    assert client.get("/events?category=nonsense").status_code == 422
    assert client.get("/events?limit=5000").status_code == 422
    assert client.get("/events?min_confidence=9").status_code == 422


def test_search_matches_body_and_respects_filters(client, make_article):
    make_article(title="Order update", body="engine plant expansion in Ghent", categories=["investments"])
    make_article(title="Other story", body="unrelated content", categories=["risk"])
    hits = client.get("/search?q=Ghent").json()
    assert hits["total"] == 1
    assert hits["items"][0]["primary_category"] == "investments"
    assert client.get("/search?q=Ghent&category=risk").json()["total"] == 0
    assert client.get("/search?q=a").status_code == 422  # min_length guard


def test_date_window_uses_utc(client, make_article):
    make_article(title="Inside window", categories=["risk"],
                 published=datetime(2026, 10, 10, 12, tzinfo=timezone.utc))
    make_article(title="Outside window", categories=["risk"],
                 published=datetime(2026, 1, 2, 12, tzinfo=timezone.utc))
    body = client.get("/events?date_from=2026-10-01T00:00:00Z&date_to=2026-10-31T00:00:00Z").json()
    assert body["total"] == 1
    assert body["items"][0]["title"] == "Inside window"
    assert body["items"][0]["published_at"].endswith("Z")


def test_reference_endpoints_expose_the_watchlist(client):
    assert len(client.get("/companies?type=oem").json()) == 5
    assert len(client.get("/companies?type=tier1").json()) == 12
    mapping = client.get("/companies/supply-map").json()
    assert len(mapping) == 25
    assert {"oem", "vendor", "segment"} <= set(mapping[0])
    vocab = client.get("/filters").json()
    assert {c["value"] for c in vocab["categories"]} == {
        "risk", "technology_innovation", "compliance_regulation", "investments"
    }


def test_event_detail_and_404(client, make_article):
    article = make_article(title="Detail me", categories=["risk"])
    body = client.get(f"/events/{article.id}").json()
    assert body["title"] == "Detail me"
    assert body["categories"][0]["rationale"]
    assert client.get("/events/999999").status_code == 404


def test_analytics_and_operational_reads(client, make_article):
    make_article(title="Trend me", categories=["risk"], severity="medium")
    trends = client.get("/trends?days=365").json()
    assert sum(row["count"] for row in trends) >= 1
    stats = client.get("/stats?days=365").json()
    assert stats["corpus"]["articles"] == 1
    assert stats["by_category"][0]["category"] == "risk"
    assert sum(row["articles"] for row in client.get("/sources").json()) == 1
    assert client.get("/runs").json() == []
    assert client.get("/trends?days=9999").status_code == 422


def test_openapi_documents_every_public_route(client):
    spec = client.get("/openapi.json").json()
    assert {"/companies", "/events", "/search", "/filters", "/trends", "/stats"} <= set(spec["paths"])
    assert spec["info"]["title"] == "Automotive Signals API"
