"""Entity tagging: link each article to the watchlist companies it mentions.

Week-3 deliverable (NER). The default backend is a curated-alias matcher, which
is more accurate than a generic NER model for this task because the entity set
is closed and known in advance (17 companies). Short acronyms are matched
case-sensitively with word boundaries so "MAN" does not fire on "manufacturer".
`spacy` is available as an alternative backend when a model is installed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from signals.db.models import Article, Company, CompanyMention

_ACRONYM_MAX_LEN = 5


@dataclass(frozen=True)
class Mention:
    company_id: int
    company_name: str
    method: str
    count: int
    matched: str


def _pattern(term: str) -> re.Pattern[str]:
    escaped = re.escape(term)
    if len(term) <= _ACRONYM_MAX_LEN and term.upper() == term:
        return re.compile(rf"(?<![\w-]){escaped}(?![\w-])")
    return re.compile(rf"(?<![\w-]){escaped}(?![\w-])", re.IGNORECASE)


def build_matcher(companies: list[Company]) -> list[tuple[int, str, re.Pattern[str], str]]:
    """Compile (company_id, company_name, pattern, term) triples, longest first."""
    compiled: list[tuple[int, str, re.Pattern[str], str]] = []
    for company in companies:
        for term in company.match_terms:
            if len(term) < 2:
                continue
            compiled.append((company.id, company.name, _pattern(term), term))
    compiled.sort(key=lambda row: len(row[3]), reverse=True)
    return compiled


def tag_text(text: str, matcher: list[tuple[int, str, re.Pattern[str], str]]) -> list[Mention]:
    if not text:
        return []
    # matcher is longest-term-first, so a long alias consumes the region a short
    # one would otherwise match again ("Volvo Trucks" wins over "Volvo").
    consumed: dict[int, list[tuple[int, int]]] = {}
    evidence: dict[int, list] = {}  # cid -> [name, total_hits, best_term, best_hits]
    for company_id, company_name, pattern, term in matcher:
        taken = consumed.setdefault(company_id, [])
        spans = [
            match.span()
            for match in pattern.finditer(text)
            if not any(start < match.end() and match.start() < end for start, end in taken)
        ]
        if not spans:
            continue
        taken.extend(spans)
        row = evidence.setdefault(company_id, [company_name, 0, "", 0])
        row[1] += len(spans)
        if (len(spans), len(term)) >= (row[3], len(row[2])):
            row[2], row[3] = term, len(spans)
    return [
        Mention(
            company_id=cid,
            company_name=row[0],
            method="alias",
            count=row[1],
            matched=row[2],
        )
        for cid, row in evidence.items()
    ]


def tag_article(session: Session, article: Article, matcher) -> list[Mention]:
    text = " ".join(filter(None, [article.title, article.clean_text, article.summary]))
    mentions = tag_text(text, matcher)
    existing = {
        m.company_id: m
        for m in session.scalars(
            select(CompanyMention).where(CompanyMention.article_id == article.id)
        )
    }
    for mention in mentions:
        row = existing.get(mention.company_id)
        if row is None:
            session.add(
                CompanyMention(
                    article_id=article.id,
                    company_id=mention.company_id,
                    method=mention.method,
                    mention_count=mention.count,
                )
            )
        else:
            row.mention_count = mention.count
            row.method = mention.method
    session.flush()
    return mentions


def load_matcher(session: Session) -> list[tuple[int, str, re.Pattern[str], str]]:
    companies = list(session.scalars(select(Company).order_by(Company.id)))
    return build_matcher(companies)
