"""Operator CLI: run any pipeline stage by hand.

    python -m signals.cli seed
    python -m signals.cli ingest --limit 20
    python -m signals.cli pipeline
"""

from __future__ import annotations

import argparse

from sqlalchemy import func, select

from signals.classify.runner import run_classification
from signals.config import get_settings
from signals.db.models import Article, Classification, Company, EventLabel, IngestRun, Source
from signals.db.session import create_all, session_scope
from signals.etl.runner import run_etl
from signals.ingest.runner import run_ingestion
from signals.logging_setup import get_logger, setup_logging
from signals.seed import seed_companies, seed_sources

log = get_logger("signals.cli")


def cmd_init_db(_: argparse.Namespace) -> None:
    create_all()
    print("schema ready")


def cmd_seed(_: argparse.Namespace) -> None:
    create_all()
    with session_scope() as session:
        companies = seed_companies(session)
        n = seed_sources(session, companies)
        print(f"companies={len(companies)} sources={n}")


def cmd_ingest(args: argparse.Namespace) -> None:
    with session_scope() as session:
        run = run_ingestion(session, source_ids=args.source or None, limit=args.limit)
        print(f"run={run.id} status={run.status} seen={run.items_seen} new={run.items_new}")
        if run.error:
            print(f"errors: {run.error[:400]}")


def cmd_etl(args: argparse.Namespace) -> None:
    with session_scope() as session:
        print(run_etl(session, limit=args.limit, retag=args.retag))


def cmd_classify(args: argparse.Namespace) -> None:
    with session_scope() as session:
        run = run_classification(session, limit=args.limit)
        print(f"run={run.id} status={run.status} {run.detail}")


def cmd_pipeline(args: argparse.Namespace) -> None:
    with session_scope() as session:
        run = run_ingestion(session, limit=args.limit)
        print(f"ingest: status={run.status} new={run.items_new}")
    with session_scope() as session:
        print("etl:", run_etl(session))
    with session_scope() as session:
        run = run_classification(session)
        print(f"classify: {run.detail}")


def cmd_stats(_: argparse.Namespace) -> None:
    settings = get_settings()
    with session_scope() as session:
        counts = {
            "companies": session.scalar(select(func.count(Company.id))),
            "sources": session.scalar(select(func.count(Source.id))),
            "enabled_sources": session.scalar(
                select(func.count(Source.id)).where(Source.enabled.is_(True))
            ),
            "articles": session.scalar(select(func.count(Article.id))),
            "classified_articles": session.scalar(
                select(func.count(func.distinct(Classification.article_id)))
            ),
            "verdicts": session.scalar(select(func.count(Classification.id))),
            "labels": session.scalar(select(func.count(EventLabel.id))),
            "runs": session.scalar(select(func.count(IngestRun.id))),
            "provider": settings.llm_provider,
            "database": settings.database_url.split("://")[0],
        }
        for key, value in counts.items():
            print(f"{key:>18}: {value}")
        by_category = session.execute(
            select(EventLabel.category, func.count(EventLabel.id)).group_by(EventLabel.category)
        ).all()
        print("by_category:", dict(by_category) or "n/a")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="signals.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db").set_defaults(func=cmd_init_db)
    sub.add_parser("seed").set_defaults(func=cmd_seed)

    p_ingest = sub.add_parser("ingest")
    p_ingest.add_argument("--limit", type=int, default=None)
    p_ingest.add_argument("--source", action="append", type=int, default=[])
    p_ingest.set_defaults(func=cmd_ingest)

    p_etl = sub.add_parser("etl")
    p_etl.add_argument("--limit", type=int, default=2000)
    p_etl.add_argument("--retag", action="store_true")
    p_etl.set_defaults(func=cmd_etl)

    p_cls = sub.add_parser("classify")
    p_cls.add_argument("--limit", type=int, default=None)
    p_cls.set_defaults(func=cmd_classify)

    p_pipe = sub.add_parser("pipeline")
    p_pipe.add_argument("--limit", type=int, default=None)
    p_pipe.set_defaults(func=cmd_pipeline)

    sub.add_parser("stats").set_defaults(func=cmd_stats)
    return parser


def main(argv: list[str] | None = None) -> None:
    setup_logging()
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
