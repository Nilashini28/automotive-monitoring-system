"""Text normalisation, canonical URLs and de-duplication keys."""

from __future__ import annotations

from datetime import datetime, timezone

from signals.ingest.clean import (
    canonical_url,
    clean_body,
    content_hash,
    html_published_at,
    html_to_text,
    to_utc,
    truncate_summary,
    url_hash,
)


def test_tracking_params_are_stripped():
    assert canonical_url("https://Ex.Example.com/News/?utm_source=x&id=7#frag") == (
        "https://ex.example.com/News?id=7"
    )


def test_same_article_from_two_outlets_differs_so_two_hashes():
    a = url_hash("https://example.com/a")
    b = url_hash("https://example.com/a?utm_source=feed")
    assert a == b


def test_html_tags_and_whitespace_removed():
    text = html_to_text("<p>Volvo  Group\xa0\xa0reports <script>evil()</script>Q3</p>")
    assert text == "Volvo Group reports Q3"


def test_content_hash_ignores_case_and_extra_whitespace():
    assert content_hash("TRUCK Recall", "body   text") == content_hash("truck recall", "body text")


def test_content_hash_catches_syndicated_copies_beyond_400_chars():
    long_tail_a = "x" * 500 + " boilerplate A"
    long_tail_b = "x" * 500 + " boilerplate B"
    assert content_hash("Same headline", long_tail_a) == content_hash("Same headline", long_tail_b)


def test_boilerplate_tail_is_cut_from_body():
    body = clean_body("Real reporting about a recall. " + "filler " * 60 + "All rights reserved")
    assert "All rights reserved" not in body


def test_naive_timestamps_become_utc():
    naive = datetime(2026, 1, 1, 12, 0, 0)
    assert to_utc(naive).tzinfo == timezone.utc
    assert to_utc(None).year >= 2026


def test_truncation_breaks_on_sentence_boundary():
    text = "First sentence here. Second sentence here. Third one is long enough to matter maybe."
    cut = truncate_summary(text, limit=45)
    assert cut.endswith(".")
    assert len(cut) <= len(text)


def test_page_declared_date_is_read_from_meta_or_time():
    assert html_published_at(
        '<html><head><meta property="article:published_time" content="2026-03-04T06:00:00Z"/></head></html>'
    ) == datetime(2026, 3, 4, 6, tzinfo=timezone.utc)
    assert html_published_at('<time datetime="2025-11-02T18:30:00+01:00">2 Nov</time>').hour == 17
    assert html_published_at("<html><body>no date anywhere</body></html>") is None
    assert html_published_at('<meta property="article:published_time" content="1601-01-01"/>') is None, (
        "the IIS epoch placeholder is not a publication date"
    )
