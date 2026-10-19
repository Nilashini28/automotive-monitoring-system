"""Verify a source URL and its CSS selector before it enters the catalogue.

    python -m signals.tools.probe_sources --press-pages
    python -m signals.tools.probe_sources --url https://... --selector "article a"
    python -m signals.tools.probe_sources --source-id 31

The seeder refuses to enable a newsroom source until its selector is proven,
because a wrong selector yields zero items and the ingest ledger happily records
a successful run with nothing in it. This tool reports the yield per selector so
that decision is made on evidence.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import httpx
import yaml
from bs4 import BeautifulSoup
from sqlalchemy import select

from signals.config import PROJECT_ROOT
from signals.db.models import Source
from signals.db.session import session_scope
from signals.ingest.fetch import build_client, fetch
from signals.ingest.parsers import parse_press_page, parse_rss, parse_sec_edgar

# Tried when the configured selector finds nothing, to suggest what the page
# actually uses.
_FALLBACK_SELECTORS = [
    "article a",
    ".news-item a",
    ".news-list a",
    ".release-list a",
    ".press-release a",
    "main li a",
    "ul li a",
]


def _press_pages() -> list[dict]:
    with (PROJECT_ROOT / "data" / "sources.yaml").open(encoding="utf-8") as fh:
        return yaml.safe_load(fh).get("press_pages", []) or []


def _suggest(client: httpx.Client, url: str) -> None:
    response = fetch(client, url)
    soup = BeautifulSoup(response.text, "html.parser")
    for selector in _FALLBACK_SELECTORS:
        anchors = [
            anchor
            for anchor in soup.select(selector)
            if len(anchor.get_text(" ", strip=True) or "") >= 25
        ]
        if len(anchors) >= 3:
            print(f"    candidate selector {selector!r}: {len(anchors)} anchors")
            for anchor in anchors[:3]:
                print(f"      - {(anchor.get_text(' ', strip=True) or '')[:90]}")
            return
    print("    no candidate selector reached 3 anchors - page is JS-rendered")


def probe(client: httpx.Client, url: str, selector: str | None, parser: str = "press_page") -> bool:
    try:
        response = fetch(client, url)
    except (httpx.HTTPError, ValueError) as exc:
        print(f"  FETCH FAILED {type(exc).__name__}: {exc}")
        return False

    ctype = response.headers.get("content-type", "?")
    print(f"  HTTP {response.status_code} {ctype} {len(response.content)} bytes")
    if response.status_code >= 400:
        return False

    if parser == "rss" or "xml" in ctype:
        items = parse_rss(response.content, 5)
    elif parser == "sec_edgar":
        items = parse_sec_edgar(response.content, 5)
    else:
        items = parse_press_page(response.content, 5, selector, url)

    print(f"  parser={parser} items={len(items)}")
    for item in items[:3]:
        date = item.published_at.date() if item.published_at else "undated"
        print(f"    - [{date}] {item.title[:80]}\n      {item.url[:110]}")
    if not items:
        _suggest(client, url)
    return bool(items)


def main(argv: list[str] | None = None) -> int:
    # Newsroom pages carry subscripts, non-breaking spaces and em-dashes; the
    # Windows console default codec would abort the probe halfway through.
    sys.stdout.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", help="single URL to probe")
    parser.add_argument("--selector", default=None, help="CSS selector for press pages")
    parser.add_argument("--parser", default="press_page", choices=["press_page", "rss", "google_news", "sec_edgar"])
    parser.add_argument("--press-pages", action="store_true", help="probe every data/sources.yaml newsroom entry")
    parser.add_argument("--source-id", type=int, help="probe one row of the `sources` table")
    args = parser.parse_args(argv)

    with build_client() as client:
        if args.url:
            return 0 if probe(client, args.url, args.selector, args.parser) else 1

        if args.source_id:
            with session_scope() as session:
                source = session.get(Source, args.source_id)
            if source is None:
                print(f"no source with id {args.source_id}")
                return 2
            print(f"{source.name} [{source.parser}] {source.url}")
            return 0 if probe(client, source.url, source.query, source.parser) else 1

        entries = _press_pages() if args.press_pages else []
        if not entries:
            print("nothing to probe - pass --url, --source-id or --press-pages")
            return 2
        failed = 0
        for entry in entries:
            print(f"\n{entry['name']}")
            print(f"  selector {entry.get('item_selector')!r}")
            if not probe(client, entry["url"], entry.get("item_selector")):
                failed += 1
        print(f"\n{len(entries) - failed}/{len(entries)} newsroom sources produce items")
        return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
