"""Batch de-duplication behaviour that keeps the 1k-record corpus clean."""

from __future__ import annotations

from signals.db.models import Article, Source
from signals.ingest.clean import content_hash, url_hash
from signals.ingest.dedupe import Candidate, filter_new


def _candidate(url: str, title: str, body: str = "same body") -> Candidate:
    return Candidate(url_hash=url_hash(url), title=title, body=body)


def test_first_batch_all_pass(session):
    batch = [_candidate(f"https://a.test/{i}", f"Headline {i}") for i in range(3)]
    assert len(filter_new(session, batch)) == 3


def test_tracking_param_variants_collapse(session):
    batch = [
        _candidate("https://a.test/story", "Story"),
        _candidate("https://a.test/story?utm_source=feed", "Story"),
    ]
    assert len(filter_new(session, batch)) == 1


def test_identical_body_from_different_urls_collapse(session):
    batch = [
        _candidate("https://a.test/1", "Truck recall", "identical wire copy"),
        _candidate("https://b.test/2", "Truck recall", "identical wire copy"),
    ]
    assert len(filter_new(session, batch)) == 1


def test_duplicates_inside_one_batch_are_removed_before_insert(session):
    batch = [_candidate("https://a.test/x", "Same", "body")] * 4
    assert len(filter_new(session, batch)) == 1


def test_persisted_articles_are_skipped_on_the_next_run(session):
    stored = _candidate("https://a.test/keep", "Kept headline", "body one")
    source = Source(name="src", kind="rss", parser="rss", url="https://src.test/rss")
    session.add(source)
    session.flush()
    session.add(
        Article(
            source_id=source.id,
            url="https://a.test/keep",
            url_hash=stored.url_hash,
            content_hash=content_hash(stored.title, stored.body),
            title=stored.title,
            clean_text=stored.body,
            word_count=2,
        )
    )
    session.flush()
    fresh = _candidate("https://a.test/new", "Fresh headline", "different body")
    assert filter_new(session, [stored, fresh]) == [fresh]
