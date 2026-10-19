"""FastAPI service (Week-5 deliverable): the only door into the corpus.

Endpoints: /companies /events /search /filters plus trend, stats, source-health
and run-ledger reads used by the dashboard and the final report.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from signals import __version__
from signals.api import queries
from signals.api.schemas import CompanyOut, EventOut, PageOut, event_out
from signals.config import get_settings
from signals.db.models import CATEGORIES, Company
from signals.db.session import get_session
from signals.logging_setup import setup_logging

SessionDep = Annotated[Session, Depends(get_session)]
SEVERITIES = ("low", "medium", "high")


def _validate(categories: list[str], severities: list[str]) -> None:
    bad = [c for c in categories if c not in CATEGORIES]
    if bad:
        raise HTTPException(status_code=422, detail=f"unknown category: {', '.join(bad)}")
    bad_sev = [s for s in severities if s not in SEVERITIES]
    if bad_sev:
        raise HTTPException(status_code=422, detail=f"unknown severity: {', '.join(bad_sev)}")

DESCRIPTION = """
Automotive Signals API. Monitors press releases, news and regulatory
announcements for the top-5 global commercial-vehicle OEMs and their Tier-1
vendors, then classifies every item as Risk, Technology & Innovation,
Compliance & Regulation or Investments.

All timestamps are UTC. Use `/filters` to discover valid filter values.
"""


def create_app() -> FastAPI:
    setup_logging()
    settings = get_settings()
    app = FastAPI(
        title="Automotive Signals API",
        version=__version__,
        description=DESCRIPTION,
        contact={"name": "Automotive Signals - internship project"},
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["GET", "OPTIONS"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def api_key_guard(request: Request, call_next):
        """Optional shared-secret gate; off by default so the demo stays public."""
        if settings.api_token and request.url.path.startswith(("/events", "/search", "/companies")):
            if request.headers.get("x-api-key") != settings.api_token:
                return JSONResponse(status_code=401, content={"detail": "invalid or missing X-API-Key"})
        return await call_next(request)

    @app.get("/healthz", tags=["system"])
    def healthz() -> dict:
        return {"status": "ok", "version": __version__, "provider": settings.llm_provider}

    @app.get("/filters", tags=["meta"], summary="Filter vocabulary for clients")
    def filters(session: SessionDep) -> dict:
        stats = queries.corpus_stats(session)
        return {
            "categories": [{"value": c, "label": _label(c)} for c in CATEGORIES],
            "severities": ["low", "medium", "high"],
            "companies": queries.company_options(session),
            "date_range": {
                "first_event_at": stats["first_event_at"],
                "latest_event_at": stats["latest_event_at"],
            },
        }

    @app.get("/companies", response_model=list[CompanyOut], tags=["reference"])
    def companies(
        session: SessionDep,
        type: Annotated[str | None, Query(description="oem or tier1")] = None,
    ) -> list[Company]:
        return queries.list_companies(session, type)

    @app.get("/companies/supply-map", tags=["reference"])
    def supply_map(session: SessionDep) -> list[dict]:
        return queries.supply_map(session)

    @app.get("/companies/{company_id}", response_model=CompanyOut, tags=["reference"])
    def company(company_id: int, session: SessionDep) -> Company:
        row = session.get(Company, company_id)
        if row is None:
            raise HTTPException(status_code=404, detail="company not found")
        return row

    @app.get("/events", response_model=PageOut, tags=["signals"], summary="Filtered event feed")
    def events(
        session: SessionDep,
        company: Annotated[list[int], Query(description="company id, repeatable")] = [],
        category: Annotated[list[str], Query(description="e.g. risk, repeatable")] = [],
        severity: Annotated[list[str], Query(description="low|medium|high, repeatable")] = [],
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        min_confidence: Annotated[float | None, Query(ge=0, le=1)] = None,
        highlight_only: bool = False,
        limit: Annotated[int, Query(ge=1, le=queries.MAX_PAGE)] = 25,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> PageOut:
        _validate(category, severity)
        rows, total = queries.query_events(
            session,
            company_ids=company or None,
            category=category or None,
            severity=severity or None,
            date_from=date_from,
            date_to=date_to,
            min_confidence=min_confidence,
            highlight_only=highlight_only,
            limit=limit,
            offset=offset,
        )
        return PageOut(
            items=[event_out(r) for r in rows], total=total, limit=limit, offset=offset
        )

    @app.get("/search", response_model=PageOut, tags=["signals"], summary="Full-text search")
    def search(
        session: SessionDep,
        q: Annotated[str, Query(min_length=2, description="matches title and body")],
        category: Annotated[list[str], Query(description="repeatable")] = [],
        company: Annotated[list[int], Query(description="repeatable")] = [],
        severity: Annotated[list[str], Query(description="repeatable")] = [],
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        min_confidence: Annotated[float | None, Query(ge=0, le=1)] = None,
        highlight_only: bool = False,
        limit: Annotated[int, Query(ge=1, le=queries.MAX_PAGE)] = 25,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> PageOut:
        """Same filter surface as `/events`, plus a text match, so search results
        stay comparable with the browsed feed."""
        _validate(category, severity)
        rows, total = queries.query_events(
            session,
            company_ids=company or None,
            category=category or None,
            q=q,
            severity=severity or None,
            date_from=date_from,
            date_to=date_to,
            min_confidence=min_confidence,
            highlight_only=highlight_only,
            limit=limit,
            offset=offset,
        )
        return PageOut(items=[event_out(r) for r in rows], total=total, limit=limit, offset=offset)

    @app.get("/events/{article_id}", response_model=EventOut, tags=["signals"])
    def event(article_id: int, session: SessionDep) -> EventOut:
        row = queries.get_event(session, article_id)
        if row is None:
            raise HTTPException(status_code=404, detail="event not found")
        return event_out(row)

    @app.get("/trends", tags=["analytics"], summary="Event counts per day and category")
    def trends(
        session: SessionDep,
        days: Annotated[int, Query(ge=1, le=365)] = 30,
        category: str | None = None,
    ) -> list[dict]:
        return queries.trends(session, days=days, category=category)

    @app.get("/stats", tags=["analytics"])
    def stats(
        session: SessionDep,
        days: Annotated[int, Query(ge=1, le=365)] = 30,
    ) -> dict:
        return {
            "corpus": queries.corpus_stats(session),
            "by_category": queries.category_summary(session, days=days),
            "top_companies": queries.company_leaderboard(session, days=days)[:12],
        }

    @app.get("/sources", tags=["system"], summary="Per-source collection health")
    def sources(session: SessionDep) -> list[dict]:
        return queries.source_health(session)

    @app.get("/runs", tags=["system"], summary="Recent pipeline runs")
    def runs(session: SessionDep, limit: Annotated[int, Query(ge=1, le=100)] = 20) -> list[dict]:
        return queries.recent_runs(session, limit)

    return app


def _label(category: str) -> str:
    from signals.classify.prompts import CATEGORY_LABELS

    return CATEGORY_LABELS.get(category, category)


app = create_app()
