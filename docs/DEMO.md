# Demo walkthrough

Ten minutes, five steps, one terminal per service. Assumes
`python -m signals.cli pipeline` has been run once (it has, on this machine:
994 articles collected and classified).

## 0. Start the two surfaces

```bash
PYTHONPATH=src uvicorn signals.api.app:app --port 8000      # terminal 1
PYTHONPATH=src streamlit run src/signals/web/app.py         # terminal 2
```

## 1. The watchlist (Week 1 deliverable)

Open `data/companies.yaml` and point at the shape: five OEMs, twelve Tier-1
vendors, each with `aliases` (which the entity tagger uses) and a `suppliers`
list. Then show the same thing as data:

```
curl "http://localhost:8000/companies/supply-map" | python -m json.tool | head -20
```

Say: *the mapping sheet is not a spreadsheet - it is a table (`supply_links`,
25 rows) with an endpoint, so the dashboard's vendor filter and the OEM roll-up
cannot disagree with it.*

## 2. Collection is bounded and auditable (Weeks 2, 7)

```
curl -s "http://localhost:8000/runs?limit=6" | python -m json.tool
```

Read out one `ingest` row (`items_seen` vs `items_new`) and one `classify` row,
and point at `sources.last_error` for a source that is down.

Say: *875 items were seen on the last pass and 15 were new - that is the dedupe
doing its job, not a broken collector. The ledger is why the worker can be killed
at any moment and restarted.*

## 3. Structure, not a blob (Week 3)

```
curl -s "http://localhost:8000/events?limit=1&category=risk" | python -m json.tool
```

Point at the four things that make it queryable: `published_at` in UTC,
`companies` (resolved entities, not string matches - empty for an industry-wide
query, which is itself the honest answer), `categories` with a confidence and a
`rationale` per label, and `source_kind`.

Then show why the timestamp matters:

```
curl -s "http://localhost:8000/events?date_from=2010-01-01&date_to=2015-01-01&limit=3"
```

Say: *those are genuinely 2010-2014 articles. An earlier version of this pipeline
stamped them with the fetch date, which would have put a 16-year-old story at the
top of the trend chart. There is a test for that now.*

## 4. Classification, with a measured accuracy (Week 4)

```
curl -s "http://localhost:8000/stats" | python -m json.tool | head -25
PYTHONPATH=src python -m signals.eval.harness score
PYTHONPATH=src python -m signals.eval.harness score --model rules-lex-v1
```

Say: *macro F1 0.837 against the rules baseline's 0.671 on the same 23 hand-
labelled rows, and both numbers come from the same table because verdicts are
stored per `prompt_version` + `model`. 23 rows is a validation set, not a
benchmark - that limitation is written down in `docs/JUSTIFICATION.md`.*

Also point at one honest failure while it is on screen: a headline like
`Volvo Trucks unveils new truck built for North America's toughest jobs` scores
Investments because the word `jobs` is in the investment lexicon.

## 5. The dashboard (Weeks 5-6)

In the browser: pick one OEM + one vendor, filter to Risk, drag the confidence
floor to 0.6, and watch the KPI row, the trend chart and the feed move together.
Then open `http://localhost:8000/docs` and point out that every widget on the
page is a call to that same public contract - the dashboard does not touch the
database.

Close by showing what is *not* claimed:
`docs/JUSTIFICATION.md` lists the limitation of every choice, and
`docs/PROGRESS_LOG.md` lists the twelve things that broke.

## Reference values seen during this walkthrough

| check | value |
| --- | --- |
| `/stats` corpus | 994 articles, 994 verdicts, 465 labels, 29 sources (26 enabled), 17 companies |
| category mix | 199 Technology & Innovation, 94 Investments, 89 Compliance & Regulation, 83 Risk |
| `eval.harness score` | macro F1 0.837, exact-set 0.739, micro P 0.833 / R 0.870 |
| last ingest run | seen=875, new=15, status=success |
| `pytest` | 57 passed, no network, no API key |
