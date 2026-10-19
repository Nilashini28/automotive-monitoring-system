"""Test wiring: an isolated SQLite file, seeded watchlist, API test client.

The database URL is set before `signals.config` is imported anywhere, because
settings are cached at first use.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

_TMP = tempfile.mkdtemp(prefix="signals-tests-")
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP}/test.db"
os.environ["LLM_PROVIDER"] = "rules"
os.environ["API_TOKEN"] = ""

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from signals.db.models import Base  # noqa: E402
from signals.db.session import SessionLocal, engine  # noqa: E402
from signals.seed import seed_companies, seed_sources  # noqa: E402


@pytest.fixture(autouse=True)
def clean_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield


@pytest.fixture
def session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def watchlist(session):
    """The real data/companies.yaml + data/sources.yaml, as production uses them."""
    companies = seed_companies(session)
    seed_sources(session, companies)
    session.commit()
    return companies


@pytest.fixture
def client(watchlist):
    from signals.api.app import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def make_article(session, watchlist):
    """Factory inserting a source + article + optional classification labels."""

    from datetime import datetime, timezone

    from signals.db.models import Article, Classification, Company, EventLabel, Source
    from signals.ingest.clean import content_hash, url_hash

    counter = {"n": 0}

    def _make(title="Sample headline", body="", company=None, categories=(), published=None,
              severity=None, confidence=0.7):
        counter["n"] += 1
        url = f"https://example.test/a{counter['n']}"
        source = Source(name="test source", kind="rss", parser="rss", url=f"https://example.test/src{counter['n']}")
        session.add(source)
        session.flush()
        article = Article(
            source_id=source.id,
            url=url,
            url_hash=url_hash(url),
            content_hash=content_hash(title, body),
            title=title,
            clean_text=body or title,
            summary=body or title,
            word_count=len((body or title).split()),
            published_at=published or datetime(2026, 10, 1, tzinfo=timezone.utc),
        )
        session.add(article)
        session.flush()
        if company:
            from signals.db.models import CompanyMention

            target = session.query(Company).filter_by(name=company).one() if isinstance(company, str) else company
            session.add(CompanyMention(article_id=article.id, company_id=target.id))
        if categories:
            cls = Classification(
                article_id=article.id, model="test-model", prompt_version="v1",
                categories=[{"category": c, "confidence": confidence, "rationale": "test"} for c in categories],
                primary_category=categories[0], confidence=confidence, severity=severity,
            )
            session.add(cls)
            session.flush()
            for c in categories:
                session.add(EventLabel(classification_id=cls.id, article_id=article.id, category=c, confidence=confidence))
        session.commit()
        return article

    return _make
