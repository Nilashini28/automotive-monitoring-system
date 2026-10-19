"""Text normalisation and de-duplication keys."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from bs4 import BeautifulSoup

_TRACKING_PARAMS = re.compile(r"^(utm_|gs_|gsource|mc_|ref|source|fb_action)")
_WS = re.compile(r"\s+")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def canonical_url(url: str) -> str:
    """Drop tracking params so syndicated copies collapse to one row."""
    parsed = urlparse(url)
    kept = [
        (k, v)
        for k, v in parse_qsl(parsed.query, keep_blank_values=True)
        if not _TRACKING_PARAMS.match(k.lower())
    ]
    return urlunparse(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            parsed.path.rstrip("/"),
            parsed.params,
            urlencode(kept),
            "",
        )
    )


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", "ignore")).hexdigest()


def url_hash(url: str) -> str:
    return sha256(canonical_url(url))


def content_hash(title: str, body: str) -> str:
    """Title + first 400 normalised chars: catches re-published press releases
    that differ only in boilerplate footer."""
    norm = _WS.sub(" ", f"{title} {body}").strip().lower()[:400]
    return sha256(norm)


def html_to_text(raw: str) -> str:
    if not raw:
        return ""
    soup = BeautifulSoup(raw, "lxml" if _lxml_available() else "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "aside", "form", "noscript"]):
        tag.decompose()
    text = soup.get_text(" ")
    return _WS.sub(" ", text).strip()


def clean_body(raw: str) -> str:
    """HTML or plaintext to single-spaced text, boilerplate tail removed."""
    text = html_to_text(raw) if "<" in raw and ">" in raw else _WS.sub(" ", raw).strip()
    for marker in (
        "Click to enlarge",
        "Share this article",
        "All rights reserved",
        "Subscribe to",
        "Cookie policy",
    ):
        idx = text.lower().find(marker.lower())
        if idx > 200:
            text = text[:idx].strip()
    return text


def html_published_at(raw: str) -> datetime | None:
    """Publication date a release page declares about itself, if any.

    Index-page parsers only see titles, and the ingest runner would otherwise
    stamp the fetch time - which turns a 2019 press release into this week's
    news in the trend chart. Meta tags are the cheapest honest fallback.
    """
    if "<" not in raw or ">" not in raw:
        return None
    soup = BeautifulSoup(raw, "html.parser")
    candidates = [tag.get("content") for tag in soup.find_all("meta") if tag.get("content")]
    candidates += [tag.get("datetime") for tag in soup.find_all("time") if tag.get("datetime")]
    for value in candidates:
        text = (value or "").strip().replace("Z", "+00:00")[:25]
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            continue
        if parsed.year < 1990:
            continue
        return to_utc(parsed)
    return None


def to_utc(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def truncate_summary(text: str, limit: int = 600) -> str:
    """Sentence-aware truncation used as the fallback body for RSS items."""
    if len(text) <= limit:
        return text
    out = ""
    for sentence in _SENTENCE_END.split(text):
        if len(out) + len(sentence) > limit and out:
            break
        out = f"{out} {sentence}".strip()
    return out or text[:limit].rsplit(" ", 1)[0]


_LXML: bool | None = None


def _lxml_available() -> bool:
    global _LXML
    if _LXML is None:
        try:
            import lxml  # noqa: F401

            _LXML = True
        except ImportError:
            _LXML = False
    return _LXML
