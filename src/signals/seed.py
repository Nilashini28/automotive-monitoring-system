"""Seed the watchlist (companies, Tier-1 links) and the source catalogue.

Idempotent: safe to re-run after editing the YAML files.
Usage: python -m signals.seed
"""

from __future__ import annotations

import argparse
from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from signals.config import PROJECT_ROOT
from signals.db.models import Company, Source, SupplyLink
from signals.db.session import create_all, session_scope

DATA_DIR = PROJECT_ROOT / "data"
GOOGLE_NEWS_RSS = "https://news.google.com/rss/search?q={query}&hl=en&gl=US&ceid=US:en"
# `ciks` is what makes this a watchlist feed: the free-text endpoint alone also
# returns every filing that merely mentions the company name.
SEC_EDGAR_FTS = (
    "https://efts.sec.gov/LATEST/search-index?q=%22%22&ciks={cik}"
    "&forms=8-K,10-K,10-Q,DEF 14A"
)


def _load(name: str) -> dict:
    path = DATA_DIR / name
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _upsert_company(session: Session, entry: dict) -> Company:
    company = session.scalar(select(Company).where(Company.name == entry["name"]))
    if company is None:
        company = Company(name=entry["name"])
        session.add(company)
    company.short_name = entry["short_name"]
    company.type = entry["type"]
    company.country = entry.get("country")
    company.group = entry.get("group")
    company.aliases = entry.get("aliases", [])
    company.notes = entry.get("notes")
    session.flush()
    return company


def seed_companies(session: Session) -> dict[str, Company]:
    cfg = _load("companies.yaml")
    companies: dict[str, Company] = {}
    for entry in cfg["oems"] + cfg["tier1_vendors"]:
        companies[entry["name"]] = _upsert_company(session, entry)

    for link in cfg["supply_links"]:
        oem, vendor = companies[link["oem"]], companies[link["vendor"]]
        exists = session.scalar(
            select(SupplyLink).where(SupplyLink.oem_id == oem.id, SupplyLink.tier1_id == vendor.id)
        )
        if exists is None:
            session.add(
                SupplyLink(oem_id=oem.id, tier1_id=vendor.id, segment=link.get("segment"))
            )
    session.flush()
    return companies


def _add_source(session: Session, **kwargs) -> Source:
    existing = session.scalar(
        select(Source).where(Source.url == kwargs["url"], Source.kind == kwargs["kind"])
    )
    if existing is not None:
        for key, value in kwargs.items():
            setattr(existing, key, value)
        return existing
    source = Source(**kwargs)
    session.add(source)
    session.flush()
    return source


def seed_sources(session: Session, companies: dict[str, Company]) -> int:
    cfg = _load("sources.yaml")
    query_template = cfg["company_query_template"]
    count = 0

    for name, company in companies.items():
        query = query_template.format(match=company.short_name)
        _add_source(
            session,
            company_id=company.id,
            kind="rss",
            parser="google_news",
            name=f"{company.short_name} - news",
            url=GOOGLE_NEWS_RSS.format(query=query),
            query=query,
            enabled=True,
        )
        count += 1

    for entry in cfg.get("extra_sources", []):
        company_id = None
        if entry.get("company"):
            company_id = companies[entry["company"]].id
        query = entry["query"]
        if entry["parser"] == "sec_edgar":
            url = SEC_EDGAR_FTS.format(cik=entry["cik"].zfill(10))
        else:
            url = GOOGLE_NEWS_RSS.format(query=query)
        _add_source(
            session,
            company_id=company_id,
            kind=entry["kind"],
            parser=entry["parser"],
            name=entry["name"],
            url=url,
            query=query,
            enabled=True,
        )
        count += 1

    for entry in cfg.get("press_pages", []):
        company_id = companies[entry["company"]].id if entry.get("company") else None
        _add_source(
            session,
            company_id=company_id,
            kind="press_page",
            parser="press_page",
            name=entry["name"],
            url=entry["url"],
            query=entry.get("item_selector"),
            enabled=bool(entry.get("enabled")),
        )
        count += 1

    session.flush()
    return count


def main(database_url: str | None) -> None:
    create_all()
    with session_scope() as session:
        companies = seed_companies(session)
        n_sources = seed_sources(session, companies)
        oems = sum(1 for c in companies.values() if c.type == "oem")
        links = session.scalar(select(SupplyLink).count())
        print(
            f"companies={len(companies)} (oem={oems}, tier1={len(companies) - oems}) "
            f"supply_links={links} sources={n_sources}"
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", default=None)
    args = parser.parse_args()
    main(args.database_url)
