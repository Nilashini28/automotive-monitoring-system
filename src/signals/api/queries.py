"""Read-only queries behind the public API.

Date bucketing is done in Python rather than SQL because `date_trunc`
(PostgreSQL) and `date()` (SQLite) are not the same function; aggregating a few
thousand rows in-process keeps one code path for both dialects.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, joinedload, selectinload

from signals.classify.prompts import CATEGORY_LABELS
from signals.db.models import (
    Article,
    Classification,
    Company,
    CompanyMention,
    EventLabel,
    IngestRun,
    Source,
    SupplyLink,
)

MAX_PAGE = 200


@dataclass
class EventRow:
    article: Article
    classification: Classification | None
    companies: list[Company]


def list_companies(session: Session, type_filter: str | None = None) -> list[Company]:
    query = select(Company).order_by(Company.type, Company.name)
    if type_filter:
        query = query.where(Company.type == type_filter)
    return list(session.scalars(query))


def company_options(session: Session) -> list[dict]:
    rows = session.execute(
        select(Company.id, Company.name, Company.short_name, Company.type, Company.country).order_by(
            Company.type.desc(), Company.name
        )
    ).all()
    return [
        {
            "id": r[0],
            "name": r[1],
            "short_name": r[2],
            "type": r[3],
            "country": r[4],
        }
        for r in rows
    ]


def supply_map(session: Session) -> list[dict]:
    rows = session.scalars(
        select(SupplyLink)
        .options(joinedload(SupplyLink.oem), joinedload(SupplyLink.tier1))
        .order_by(SupplyLink.oem_id, SupplyLink.tier1_id)
    )
    return [
        {
            "oem": link.oem.name,
            "oem_short": link.oem.short_name,
            "vendor": link.tier1.name,
            "vendor_short": link.tier1.short_name,
            "segment": link.segment,
        }
        for link in rows
    ]


def _base_statement():
    # selectinload (not joinedload) for the mentions collection: it keeps one row
    # per article so no result-uniquening is needed.
    return (
        select(Article)
        .options(
            joinedload(Article.source),
            selectinload(Article.mentions).joinedload(CompanyMention.company),
        )
        .where(Article.id.in_(select(Classification.article_id)))
    )


def query_events(
    session: Session,
    company_ids: list[int] | None = None,
    category: list[str] | None = None,
    q: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    severity: list[str] | None = None,
    min_confidence: float | None = None,
    highlight_only: bool = False,
    limit: int = 25,
    offset: int = 0,
) -> tuple[list[EventRow], int]:
    conditions = []
    if company_ids:
        conditions.append(
            Article.id.in_(
                select(CompanyMention.article_id).where(CompanyMention.company_id.in_(company_ids))
            )
        )
    if category:
        conditions.append(
            Article.id.in_(
                select(EventLabel.article_id).where(EventLabel.category.in_(category))
            )
        )
    if date_from:
        conditions.append(Article.published_at >= _aware(date_from))
    if date_to:
        conditions.append(Article.published_at <= _aware(date_to))
    if severity:
        conditions.append(
            Article.id.in_(
                select(Classification.article_id).where(Classification.severity.in_(severity))
            )
        )
    if min_confidence is not None:
        conditions.append(
            Article.id.in_(
                select(Classification.article_id).where(Classification.confidence >= min_confidence)
            )
        )
    if highlight_only:
        conditions.append(
            Article.id.in_(
                select(Classification.article_id).where(
                    or_(Classification.severity == "high", Classification.confidence >= 0.85)
                )
            )
        )
    if q:
        needle = f"%{q}%"
        conditions.append(or_(Article.title.ilike(needle), Article.clean_text.ilike(needle)))

    where = and_(*conditions) if conditions else None
    count_stmt = select(func.count(Article.id)).where(
        Article.id.in_(select(Classification.article_id))
    )
    total = session.scalar(count_stmt if where is None else count_stmt.where(where)) or 0

    stmt = _base_statement()
    if where is not None:
        stmt = stmt.where(where)
    stmt = stmt.order_by(Article.published_at.desc(), Article.id.desc()).limit(
        min(max(limit, 1), MAX_PAGE)
    ).offset(max(offset, 0))
    articles = list(session.scalars(stmt))
    best = _best_classifications(session, [a.id for a in articles])
    rows = [_as_row(a, best.get(a.id)) for a in articles]
    return rows, int(total)


def _best_classifications(session: Session, ids: list[int]) -> dict[int, Classification]:
    """Highest-confidence verdict per article, in one query for the whole page."""
    if not ids:
        return {}
    rows = session.scalars(
        select(Classification)
        .where(Classification.article_id.in_(ids))
        .order_by(Classification.article_id, Classification.confidence.desc(), Classification.id.desc())
    )
    out: dict[int, Classification] = {}
    for row in rows:
        out.setdefault(row.article_id, row)
    return out


def _as_row(article: Article, classification: Classification | None) -> EventRow:
    return EventRow(article=article, classification=classification, companies=[m.company for m in article.mentions if m.company])


def get_event(session: Session, article_id: int) -> EventRow | None:
    article = session.scalar(
        select(Article)
        .options(
            joinedload(Article.source),
            selectinload(Article.mentions).joinedload(CompanyMention.company),
        )
        .where(Article.id == article_id)
    )
    if article is None:
        return None
    best = _best_classifications(session, [article.id])
    return _as_row(article, best.get(article.id))


def trends(session: Session, days: int = 30, category: str | None = None) -> list[dict]:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    stmt = (
        select(Article.id, Article.published_at, EventLabel.category)
        .join(EventLabel, EventLabel.article_id == Article.id)
        .where(Article.published_at >= since)
    )
    if category:
        stmt = stmt.where(EventLabel.category == category)
    buckets: dict[tuple[str, str], int] = {}
    for _, published, cat in session.execute(stmt).all():
        if published is None:
            continue
        day = published.astimezone(timezone.utc).date().isoformat()
        buckets[(day, cat)] = buckets.get((day, cat), 0) + 1
    out = [
        {"date": day, "category": cat, "label": CATEGORY_LABELS.get(cat, cat), "count": n}
        for (day, cat), n in buckets.items()
    ]
    out.sort(key=lambda r: (r["date"], r["category"]))
    return out


def category_summary(session: Session, days: int | None = 30) -> list[dict]:
    since = None
    if days:
        since = datetime.now(timezone.utc) - timedelta(days=days)
    stmt = select(EventLabel.category, func.count(EventLabel.id)).group_by(EventLabel.category)
    if since:
        stmt = stmt.join(Article, Article.id == EventLabel.article_id).where(
            Article.published_at >= since
        )
    rows = session.execute(stmt).all()
    total = sum(r[1] for r in rows) or 1
    return [
        {
            "category": r[0],
            "label": CATEGORY_LABELS.get(r[0], r[0]),
            "count": r[1],
            "share": round(r[1] / total, 3),
        }
        for r in sorted(rows, key=lambda x: x[1], reverse=True)
    ]


def company_leaderboard(session: Session, days: int = 30, category: str | None = None) -> list[dict]:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    stmt = (
        select(Company.id, Company.short_name, Company.type, func.count(CompanyMention.id))
        .join(CompanyMention, CompanyMention.company_id == Company.id)
        .join(Article, Article.id == CompanyMention.article_id)
        .where(Article.published_at >= since)
        .group_by(Company.id, Company.short_name, Company.type)
        .order_by(func.count(CompanyMention.id).desc())
    )
    if category:
        stmt = stmt.where(
            Article.id.in_(select(EventLabel.article_id).where(EventLabel.category == category))
        )
    return [
        {"company_id": r[0], "company": r[1], "type": r[2], "events": r[3]}
        for r in session.execute(stmt).all()
    ]


def source_health(session: Session) -> list[dict]:
    rows = session.execute(
        select(Source.id, Source.name, Source.kind, Source.parser, Source.enabled,
               Source.last_success_at, Source.last_error, func.count(Article.id))
        .outerjoin(Article, Article.source_id == Source.id)
        .group_by(Source.id)
        .order_by(Source.id)
    ).all()
    return [
        {
            "id": r[0], "name": r[1], "kind": r[2], "parser": r[3], "enabled": r[4],
            "last_success_at": r[5], "last_error": r[6], "articles": r[7],
        }
        for r in rows
    ]


def recent_runs(session: Session, limit: int = 20) -> list[dict]:
    rows = session.scalars(select(IngestRun).order_by(IngestRun.id.desc()).limit(limit))
    return [
        {
            "id": r.id, "kind": r.kind, "status": r.status, "started_at": r.started_at,
            "finished_at": r.finished_at, "items_seen": r.items_seen, "items_new": r.items_new,
            "detail": r.detail,
        }
        for r in rows
    ]


def corpus_stats(session: Session) -> dict:
    first = session.scalar(select(func.min(Article.published_at)))
    last = session.scalar(select(func.max(Article.published_at)))
    return {
        "companies": session.scalar(select(func.count(Company.id))),
        "sources": session.scalar(select(func.count(Source.id))),
        "articles": session.scalar(select(func.count(Article.id))),
        "classified_articles": session.scalar(
            select(func.count(func.distinct(Classification.article_id)))
        ),
        "classifications": session.scalar(select(func.count(Classification.id))),
        "labels": session.scalar(select(func.count(EventLabel.id))),
        "first_event_at": first,
        "latest_event_at": last,
    }


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
