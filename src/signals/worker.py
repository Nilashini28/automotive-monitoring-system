"""Long-running collection worker.

    python -m signals.worker

Three interval jobs, each in its own DB session so a failure in one stage cannot
leave a transaction open: collect -> structure -> classify. The run ledger in
`ingest_runs` is what makes the loop restart-safe and auditable.
"""

from __future__ import annotations

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger

from signals.classify.runner import run_classification
from signals.config import get_settings
from signals.db.session import create_all, session_scope
from signals.etl.runner import run_etl
from signals.ingest.runner import run_ingestion
from signals.logging_setup import get_logger, setup_logging

log = get_logger("signals.worker")


def job_ingest() -> None:
    with session_scope() as session:
        run = run_ingestion(session)
        log.info("ingest job: new=%s status=%s", run.items_new, run.status)


def job_etl() -> None:
    with session_scope() as session:
        log.info("etl job: %s", run_etl(session))


def job_classify() -> None:
    with session_scope() as session:
        run = run_classification(session)
        log.info("classify job: %s", run.detail)


def main() -> None:
    setup_logging()
    create_all()
    settings = get_settings()
    scheduler = BlockingScheduler(timezone="UTC")
    scheduler.add_job(
        job_ingest, IntervalTrigger(minutes=settings.ingest_interval_minutes),
        id="ingest", max_instances=1, coalesce=True, misfire_grace_time=300,
    )
    scheduler.add_job(
        job_etl, IntervalTrigger(minutes=max(5, settings.ingest_interval_minutes - 5)),
        id="etl", max_instances=1, coalesce=True, misfire_grace_time=300,
    )
    scheduler.add_job(
        job_classify, IntervalTrigger(minutes=settings.classify_interval_minutes),
        id="classify", max_instances=1, coalesce=True, misfire_grace_time=300,
    )
    log.info(
        "worker started: ingest=%sm etl->classify=%sm provider=%s",
        settings.ingest_interval_minutes,
        settings.classify_interval_minutes,
        settings.llm_provider,
    )
    job_ingest()
    job_etl()
    job_classify()
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("worker stopping")


if __name__ == "__main__":
    main()
