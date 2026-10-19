"""Set-based de-duplication: one query per batch, never one per item."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from signals.db.models import Article
from signals.ingest.clean import content_hash


@dataclass(frozen=True)
class Candidate:
    url_hash: str
    title: str
    body: str | None
    item: Any = field(default=None, compare=False)

    @property
    def content_hash(self) -> str:
        return content_hash(self.title, self.body or "")


def existing_url_hashes(session: Session, hashes: Sequence[str]) -> set[str]:
    if not hashes:
        return set()
    return set(session.scalars(select(Article.url_hash).where(Article.url_hash.in_(hashes))))


def existing_content_hashes(session: Session, hashes: Sequence[str]) -> set[str]:
    if not hashes:
        return set()
    return set(
        session.scalars(select(Article.content_hash).where(Article.content_hash.in_(hashes)))
    )


def filter_new(session: Session, candidates: Sequence[Candidate]) -> list[Candidate]:
    """Drop candidates whose URL or body text is already stored.

    A single near-duplicate press release syndicated across five outlets becomes
    one row because both keys are checked against the whole batch at once.
    """
    if not candidates:
        return []
    known_urls = existing_url_hashes(session, [c.url_hash for c in candidates])
    known_bodies = existing_content_hashes(session, [c.content_hash for c in candidates])
    fresh: list[Candidate] = []
    taken_urls: set[str] = set()
    taken_bodies: set[str] = set()
    for cand in candidates:
        if cand.url_hash in known_urls or cand.content_hash in known_bodies:
            continue
        if cand.url_hash in taken_urls or cand.content_hash in taken_bodies:
            continue
        taken_urls.add(cand.url_hash)
        taken_bodies.add(cand.content_hash)
        fresh.append(cand)
    return fresh
