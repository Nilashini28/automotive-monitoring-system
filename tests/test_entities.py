"""Entity tagging: the alias matcher must not fire on substrings of other words."""

from __future__ import annotations

from signals.db.models import Company
from signals.etl.entities import build_matcher, tag_text


def _company(company_id: int, name, short, aliases=()):
    return Company(id=company_id, name=name, short_name=short, type="oem", aliases=list(aliases))


def test_matches_full_company_and_alias():
    matcher = build_matcher([_company(11, "AB Volvo", "Volvo Group", ["Volvo Trucks", "Mack Trucks"])])
    mentions = tag_text("Volvo Trucks parent AB Volvo said Mack Trucks will expand.", matcher)
    assert len(mentions) == 1
    assert mentions[0].count >= 3


def test_short_acronym_does_not_match_inside_a_longer_word():
    matcher = build_matcher([_company(12, "MAN Truck & Bus", "MAN", ["MAN"])])
    assert tag_text("The manufacturer reported many managements.", matcher) == []
    assert tag_text("MAN reported strong orders in Q3.", matcher) != []


def test_acronym_is_case_sensitive():
    matcher = build_matcher([_company(13, "ZF Friedrichshafen AG", "ZF", ["ZF"])])
    assert tag_text("The zf file system was reformatted.", matcher) == []
    assert tag_text("ZF opened a new plant.", matcher) != []


def test_two_companies_both_tagged_when_both_appear():
    matcher = build_matcher(
        [
            _company(21, "AB Volvo", "Volvo Group", ["Volvo Trucks"]),
            _company(22, "Cummins Inc.", "Cummins", ["Cummins"]),
        ]
    )
    found = {m.company_id for m in tag_text("Volvo Trucks will buy Cummins engines.", matcher)}
    assert found == {21, 22}
