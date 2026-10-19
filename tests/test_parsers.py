"""Parser contracts, checked against captured upstream shapes.

These exist because the first EDGAR parser guessed field names (`form_type`,
`accession_no`) that the API never returns and silently produced 187
"SEC filer filing filing" rows.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from signals.db.models import Source
from signals.ingest.parsers import parse_sec_edgar
from signals.ingest.runner import SEC_LOOKBACK_DAYS, _request_url

# Trimmed from a live https://efts.sec.gov/LATEST/search-index response.
EDGAR = json.dumps(
    {
        "hits": {
            "hits": [
                {
                    "_id": "0001564590-19-003670:pcar-ex21_7.htm",
                    "_source": {
                        "ciks": ["0000075362"],
                        "period_ending": "2018-12-31",
                        "display_names": ["PACCAR INC  (PCAR)  (CIK 0000075362)"],
                        "root_forms": ["10-K"],
                        "file_date": "2019-02-21",
                        "form": "10-K",
                        "adsh": "0001564590-19-003670",
                        "file_type": "EX-21",
                    },
                },
                {
                    "_id": "0001564590-19-003670:pcar-20181231x10k.htm",
                    "_source": {
                        "ciks": ["0000075362"],
                        "period_ending": "2018-12-31",
                        "display_names": ["PACCAR INC  (PCAR)  (CIK 0000075362)"],
                        "root_forms": ["10-K"],
                        "file_date": "2019-02-21",
                        "form": "10-K",
                        "adsh": "0001564590-19-003670",
                        "file_type": "10-K",
                    },
                },
                {
                    "_id": "0000950170-25-023456:d123456d8k.htm",
                    "_source": {
                        "ciks": ["0000802550"],
                        "period_ending": "2025-03-31",
                        "display_names": ["EATON CORP PLC  (ETN)  (CIK 0000802550)"],
                        "root_forms": ["8-K"],
                        "file_date": "2025-04-01",
                        "form": "8-K",
                        "adsh": "0000950170-25-023456",
                        "file_type": "8-K",
                    },
                },
            ]
        }
    }
).encode()


def test_filings_are_collapsed_to_one_row_per_accession():
    items = parse_sec_edgar(EDGAR, limit=10)
    assert [i.meta["accession"] for i in items] == [
        "0001564590-19-003670",
        "0000950170-25-023456",
    ], "the two documents of one filing must not become two events"


def test_real_field_names_reach_a_readable_title_and_link():
    first = parse_sec_edgar(EDGAR, limit=10)[0]
    assert first.title == "PACCAR INC 10-K filing (2019-02-21)"
    assert first.published_at == datetime(2019, 2, 21, tzinfo=timezone.utc)
    assert first.url == (
        "https://www.sec.gov/Archives/edgar/data/75362/000156459019003670/"
        "0001564590-19-003670-index.htm"
    )
    assert "PACCAR INC filed a 10-K with the SEC" in (first.summary or "")
    assert first.meta["needs_body"] is True


def test_limit_is_applied_after_collapsing():
    assert len(parse_sec_edgar(EDGAR, limit=1)) == 1


def test_edgar_requests_are_limited_to_a_recent_window():
    source = Source(name="s", kind="regulator", parser="sec_edgar",
                    url="https://efts.sec.gov/LATEST/search-index?q=%22PACCAR%22&forms=8-K")
    url = _request_url(source)
    assert "dateRange=custom" in url and "startdt=" in url
    assert f"startdt=" in url and len(url.split("startdt=")[1][:10]) == 10


def test_other_parsers_keep_their_configured_url():
    source = Source(name="s", kind="rss", parser="google_news", url="https://news.example/rss")
    assert _request_url(source) == source.url
