"""Single logging entrypoint: structured-ish console logs for every process."""

from __future__ import annotations

import logging
import sys

_FORMAT = "%(asctime)s %(levelname)-7s [%(name)s] %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"


def setup_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    if root.handlers:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(_FORMAT, _DATEFMT))
    root.addHandler(handler)
    root.setLevel(level.upper())
    for noisy in ("httpx", "urllib3", "apscheduler.executors.default", "feedparser"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
