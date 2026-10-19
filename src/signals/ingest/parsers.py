"""Per-parser-source readers that emit `RawItem`s for the ingest runner.

Each parser is deliberately tolerant: a newsroom layout change should drop one
source's yield, not crash the whole collection job.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone

import feedparser
import httpx
from bs4 import BeautifulSoup

from signals.ingest.clean import clean_body, html_to_text, to_utc
from signals.logging_setup import get_logger

log = get_logger(__name__)


@dataclass
class RawItem:
    url: str
    title: str
    summary: str | None = None
    body: str | None = None
    published_at: datetime | None = None
    lang: str = "en"
    meta: dict = field(default_factory=dict)


def _entry_published(entry) -> datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        st = entry.get(key)
        if st:
            return to_utc(datetime.fromtimestamp(calendar_timegm(st), tz=timezone.utc))
    return None


def calendar_timegm(struct_time) -> float:
    import calendar

    return calendar.timegm(struct_time)


def parse_rss(payload: bytes, limit: int) -> list[RawItem]:
    """Generic RSS/Atom reader, also used for Google News RSS."""
    feed = feedparser.parse(payload)
    items: list[RawItem] = []
    for entry in feed.entries[:limit]:
        link = entry.get("link") or entry.get("id") or ""
        if not link:
            continue
        raw_html = entry.get("content", [{}])[0].get("value") if entry.get("content") else None
        summary = entry.get("summary") or entry.get("description") or ""
        body = clean_body(raw_html) if raw_html else clean_body(summary)
        items.append(
            RawItem(
                url=link,
                title=html_to_text(entry.get("title", ""))[:600],
                summary=html_to_text(summary)[:2000],
                body=body,
                published_at=_entry_published(entry),
                lang=(entry.get("language") or feed.feed.get("language") or "en")[:10],
                meta={"feed": feed.feed.get("title", "")},
            )
        )
    return items


def parse_sec_edgar(payload: bytes, limit: int) -> list[RawItem]:
    """SEC full-text search: regulatory filings, which are events not news.

    One hit is one *document*, and a filing is published as many documents, so
    hits are collapsed per accession number. The field names here were verified
    against the live response (`form`, `adsh`, `ciks`, `display_names`) - the
    guessed ones (`form_type`, `accession_no`) do not exist and used to produce
    194 "SEC filer filing filing" rows.
    """
    data = json.loads(payload.decode("utf-8", "ignore"))
    hits = (data.get("hits") or {}).get("hits") or []
    items: list[RawItem] = []
    seen: set[str] = set()
    for hit in hits:
        src = hit.get("_source") or {}
        accession = (src.get("adsh") or "").strip()
        if not accession or accession in seen:
            continue
        seen.add(accession)
        form = src.get("form") or (src.get("root_forms") or ["filing"])[0]
        entity = ((src.get("display_names") or ["SEC filer"])[0] or "SEC filer").split("  (")[0].strip()
        published = None
        if src.get("file_date"):
            try:
                published = to_utc(datetime.fromisoformat(src["file_date"][:10]))
            except ValueError:
                published = None
        cik = ((src.get("ciks") or [""])[0] or "").lstrip("0") or "0"
        # Link the filing index, not one document: the hit list is per-document
        # and the first one is often an exhibit.
        url = (
            f"https://www.sec.gov/Archives/edgar/data/{cik}/"
            f"{accession.replace('-', '')}/{accession}-index.htm"
        )
        period = src.get("period_ending") or ""
        summary = (
            f"{entity} filed a {form} with the SEC"
            + (f" for the period ending {period}." if period else ".")
        )
        items.append(
            RawItem(
                url=url,
                title=f"{entity} {form} filing ({src.get('file_date') or 'undated'})"[:600],
                summary=summary[:2000],
                body=None,
                published_at=published,
                meta={"parser": "sec_edgar", "form_type": form, "accession": accession,
                      "needs_body": True},
            )
        )
        if len(items) >= limit:
            break
    return items


def parse_press_page(
    payload: bytes, limit: int, item_selector: str | None, base_url: str | None = None
) -> list[RawItem]:
    """Company press-release index page: link list only, body fetched later."""
    soup = BeautifulSoup(payload, "html.parser")
    selector = item_selector or "article a, .news-item a, ul li a"
    items: list[RawItem] = []
    seen: set[str] = set()
    for anchor in soup.select(selector):
        href = anchor.get("href")
        title = anchor.get_text(" ", strip=True)
        if not href or not title or len(title) < 25:
            continue
        # Newsroom indexes link relatively; an unresolved href dedupes and
        # canonicalises into something useless.
        base = httpx.URL(base_url) if base_url else None
        url = str(base.join(href)) if base is not None else href
        if url in seen:
            continue
        seen.add(url)
        items.append(
            RawItem(
                url=url,
                title=title[:600],
                summary=title[:600],
                body=None,
                published_at=None,
                meta={"parser": "press_page", "needs_body": True},
            )
        )
        if len(items) >= limit:
            break
    return items


PARSERS = {
    "google_news": lambda body, limit, query, url: parse_rss(body, limit),
    "rss": lambda body, limit, query, url: parse_rss(body, limit),
    "sec_edgar": lambda body, limit, query, url: parse_sec_edgar(body, limit),
    "press_page": lambda body, limit, query, url: parse_press_page(body, limit, query, url),
}
