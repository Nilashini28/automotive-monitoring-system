# Automotive Signals

Global monitoring of the commercial-vehicle industry: it collects press releases,
news and regulatory filings for the five largest truck/bus OEMs and their Tier-1
suppliers, structures them, classifies every event into **Risk / Technology &
Innovation / Compliance & Regulation / Investments**, and serves the result
through a REST API and a dashboard.

```
sources (RSS, SEC EDGAR)                    SQLite / PostgreSQL
      |  fetch -> clean -> dedupe                    ^
      v                                              |
  articles  --->  ETL (bodies, entities, timestamps) |
      |                                              |
      v                                              |
  classifications  --->  event_labels  ---------------+
      |
      +--> FastAPI (Swagger) --> Streamlit dashboard
```

## Quickstart (local, no Docker)

Requires Python 3.11+ (developed on 3.14). No `.env` file is needed - every
default works out of the box and the database is a file under `var/`.

```bash
python -m venv .venv
.venv/Scripts/activate            # source .venv/bin/activate on Linux/macOS
pip install -r requirements.txt

PYTHONPATH=src python -m signals.cli seed        # watchlist + source catalogue
PYTHONPATH=src python -m signals.cli pipeline    # ingest -> ETL -> classify
PYTHONPATH=src python -m signals.cli stats       # corpus counters - works as cloned
```

`var/signals.db` is committed as a checkpointed snapshot of 994 collected,
entity-tagged and classified records, so both surfaces have real data on a fresh
clone with no keys and no network. `seed` and `pipeline` are what add to it;
running `pipeline` will show the DB file as modified in git, which is expected.

Then run the two surfaces:

```bash
PYTHONPATH=src uvicorn signals.api.app:app --port 8000     # http://localhost:8000/docs
PYTHONPATH=src streamlit run src/signals/web/app.py        # http://localhost:8501
```

`PYTHONPATH=src` is required because the package is not installed - `src/` is the
source tree, not a build artifact.

## Continuous collection

The worker is the deployable service: three APScheduler jobs, each in its own
transaction, so a dead source cannot stall the pipeline.

```bash
PYTHONPATH=src python -m signals.worker
```

| knob | default | meaning |
| --- | --- | --- |
| `INGEST_INTERVAL_MINUTES` | 60 | how often every enabled source is polled |
| `CLASSIFY_INTERVAL_MINUTES` | 15 | how often new articles get a verdict |
| `LLM_PROVIDER` | `rules` | `rules` (offline) or `openai` (any OpenAI-compatible endpoint) |
| `DATABASE_URL` | `sqlite:///var/signals.db` | Postgres in production |
| `API_TOKEN` | empty | when set, `X-API-Key` is required on every API route |

Full list in [.env.example](.env.example).

## Docker

```bash
docker compose up --build       # db + api + web + worker
```

`api` runs migrations and seeds before uvicorn starts (`MIGRATE_ON_START`,
`SEED_ON_START`); `web` and `worker` wait for it and reuse the same image.
Healthchecks use `python -urllib` rather than `curl`, because the runtime image
has no curl.

## CLI

```
python -m signals.cli  init-db | seed | ingest | etl | classify | pipeline | stats
python -m signals.eval.harness  export | load | score [--model rules-lex-v3] [--min-confidence 0.6]
python -m signals.tools.probe_sources --press-pages | --url URL --selector CSS | --source-id N
```

`probe_sources` is what keeps the catalogue honest: it fetches a newsroom, prints
what the configured CSS selector actually yields and suggests better ones, so a
source is only enabled on evidence. See
[data/sources.yaml](data/sources.yaml) for the current findings.

## Tests

```bash
PYTHONPATH=src python -m pytest
```

57 tests, no network: feeds, EDGAR payloads and newsroom pages are mocked with
`respx` from captured shapes.

## Data

| file | role |
| --- | --- |
| `data/companies.yaml` | 5 OEMs + 12 Tier-1 vendors, aliases for the entity tagger, supplier links |
| `data/sources.yaml` | source catalogue: Google News RSS queries, SEC EDGAR CIK-scoped feeds, newsroom probes |
| `data/gold_labels.json` | hand-labelled validation set the metrics are computed from |
| `data/validation_template.json` | blank template produced by `eval.harness export` |

## API

`GET /healthz /filters /companies /companies/supply-map /companies/{id} /events
/events/{id} /search /trends /stats /sources /runs` - interactive docs at
`/docs`, machine-readable schema at `/openapi.json`. Offset pagination (`limit`,
`offset`) and filtering (`company`, `category`, `severity`, `date_from`,
`date_to`, `min_confidence`, `highlight_only`, all list-valued filters repeatable)
on `/events`; `/runs` exposes the ingest ledger, which is the operational liveness
signal.

## Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) - diagram, data model, invariants
- [docs/JUSTIFICATION.md](docs/JUSTIFICATION.md) - every library, tool, module and
  model choice with its pros **and its limitations**
- [docs/PROGRESS_LOG.md](docs/PROGRESS_LOG.md) - what was built each week, and what
  broke
- [docs/DEMO.md](docs/DEMO.md) - ten-minute walkthrough script with the commands and
  the reference values to expect

## Current numbers

994 articles from 26 enabled sources, 17 companies tracked, 440 of them carrying
at least one label (465 label rows: 199 Technology & Innovation, 94 Investments,
89 Compliance & Regulation, 83 Risk). Classifier `rules-lex-v3` on the 23-row
hand-labelled set: macro F1 **0.837**, exact-set accuracy **0.739**, micro
precision **0.833** / recall **0.870** (baseline `rules-lex-v1`: macro F1 0.671,
exact-set 0.478). Reproduce with `python -m signals.eval.harness score`.

The corpus travels with the repo: `var/signals.db` is a checkpointed snapshot of
the 994 collected and classified records, so the API and the dashboard have
something to show on a fresh clone with no `.env` and no network. Run `python -m
signals.cli pipeline` to collect live records into it (git will then show the file
as modified - that is expected; the app never needs the committed copy to stay
current).

Two honest caveats, both explained in
[docs/JUSTIFICATION.md](docs/JUSTIFICATION.md): the average article is **19.7
words**, because the free news source is a search surface that returns a headline
plus one sentence (only the SEC rows carry real text, restored by the enrichment
pass); and about half of all verdicts carry no label at all - the lexicon needs a
hit, and a verdict below the 0.2 confidence floor is stored but not indexed.
