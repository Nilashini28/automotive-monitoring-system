# Justified stack: choices, pros and limitations

Everything below is what the repository actually uses. Each entry states the
alternative that was rejected and the cost of the choice, because a prototype
that only lists advantages has not been tested.

## Language and runtime

| choice | pros | limitations |
| --- | --- | --- |
| **Python 3.11+ (developed on 3.14)** | one language across ingestion, ML-ish classification, API and dashboard; the ecosystem owns RSS/HTML/LLM clients | the whole app is CPU-bound-ish Python: parsing and the entity tagger are the slow parts, not the DB. Typing is checked only by tests, since no mypy/ruff gate is wired into CI |
| **`src/` layout, not installed** | zero build step; `PYTHONPATH=src` and it runs | every command needs that env var (including the Dockerfile `ENV`), and `pip install -e .` would fail because there is no `[project]` table. Deliberate: a single dependency file, `requirements.txt`, is easier to audit |
| **`requirements.txt` pinning with `>=` floors** | readable diff, easy `pip install -r` for reviewers | not a lockfile: two clean installs on different days can get different patch versions. For a reproducible demo deploy `pip freeze` into a lockfile |

## Data layer

| choice | pros | limitations |
| --- | --- | --- |
| **SQLAlchemy 2.0 ORM (`Mapped` / `mapped_column`)** | typed declarative models, one schema for SQLite and PostgreSQL, `select()` composes cleanly into the query module | the ORM hides N+1 risks (the API needs `selectinload`/`joinedload` on `Article -> Source -> Company`); bulk label rebuilds are Python loops, which cost ~0.5 s per 1000 articles |
| **SQLite for development, PostgreSQL in compose** | the reviewer clones the repo and gets a working app with no services; the file lives in `var/` | two dialects to keep honest: no JSON operators, no `ILIKE`, no `ON CONFLICT` tricks. `UTCDateTime` exists precisely because SQLite happily stores naive datetimes that PostgreSQL rejects |
| **Alembic, baseline revision created from ORM metadata** | one command to get the schema, `downgrade` exists, later revisions autogenerate against the same metadata | the baseline is a `create_all` call, so it cannot express a hand-written DDL optimization; a fresh table added to the models without a revision is invisible until someone runs `alembic check` |
| **Normalised `event_labels` instead of querying JSON** | category + date filters are plain indexed SQL and portable | the table is derived state: it must be rebuilt after every classification pass (`refresh_event_labels`). That rebuild is a full scan - acceptable at 1000 rows, worth revisiting at 100 000 |
| **Append-only `classifications`** | prompt/model versions stay comparable, verdict history is auditable | storage grows per re-run and the "current verdict" rule (highest confidence, then latest id) is a Python-side ranking, not a SQL one |

## ETL

| choice | pros | limitations |
| --- | --- | --- |
| **"Untagged means not yet processed" in `run_etl`** | no extra column, no migration, and a re-tag is automatic whenever a body arrives later | an article whose text genuinely names no watchlist company never gets a `company_mentions` row, so it is re-scanned on every pass - 332 of 994 rows were re-read by the last run. Correct, just wasteful; a `tagged_at` column would fix it and cost a migration |
| **`enrich_bodies` fetches the filing *index* page, not the primary document** | one request, no HTML-of-a-1990s-SEC-document parsing, and it restores filing metadata (form, period, filers) plus 19.7-word average text | the real filing text lives one hop further (`ex99_1.htm`); classification of SEC rows therefore reads a filing header, not the exhibit. Deliberately left as the next improvement |

## Ingestion

| choice | pros | limitations |
| --- | --- | --- |
| **`httpx` (sync client)** | one client for feeds, EDGAR, body enrichment, the API test and the Streamlit layer; timeouts, redirects and HTTP/2 in one place; `respx` mocks it exactly | a sync client blocks a worker thread per source; concurrency would need `asyncio` or a thread pool. No connection-pool tuning exposed |
| **`feedparser`** | handles RSS 2.0, Atom, weird encodings, `pubDate` variants and relative links that hand-rolled `ElementTree` gets wrong | lenient to a fault: a malformed feed returns 0 entries and no exception, so "0 items" must be detected via the run ledger, not error handling. Also effectively single-maintainer upstream |
| **`beautifulsoup4` + `lxml`, with a `html.parser` fallback** | CSS selectors, strips `script`/`nav`/`footer` boilerplate; `lxml` is ~5x faster | the CSS support of `html.parser` (the no-lxml fallback) is weaker, so a missing `lxml` degrades selector coverage silently. `soup.select()` on a 130 KB newsroom page is tens of milliseconds; on a JS-rendered SPA it returns nothing at all - which is exactly what `probe_sources` found for two of the three newsrooms |
| **Google News RSS as the news source** | free, no key, no signup, covers every company's press releases and the wires that carry them, stable URL template, and legally unambiguous to deploy | it is a *search* surface, not a publisher API: titles are truncated, `link` points at a `news.google.com` redirect rather than the article, and the summary is one sentence - the average article in the corpus is 19.7 words and RSS-only rows are ~13. Query syntax (`"{name}" (truck OR bus)`) is Google's and can change without notice |
| **Rejected: NewsAPI** | excellent DX, JSON, multi-source dedupe already done | the free developer tier is licensed for development only and forbids production/deployed use, and it lags published articles by ~24 h. Using it would have made the "deployable" half of the brief false |
| **SEC EDGAR full-text search (`efts.sec.gov`)** | authoritative regulatory filings, free, CIK-scoped so the results are actually the company's own filings | CIK numbers have to be curated by hand in `data/sources.yaml` (free-text search alone returned Clorox and floorplan-trust filings under a "Ford" source); the endpoint requires a real `User-Agent` and throttles politely; the FTS payload gives metadata, not filing text, so bodies come from a second fetch of the filing index page |
| **`tenacity` for retries** | declarative backoff, one policy shared by every fetcher | retries mask a permanently dead source; that is why `sources.last_error` and the ledger exist next to it |
| **Canonical-URL + content-hash dedupe** | catches syndicated wire copy that differs only in boilerplate, and `?utm_*` variants | the content hash covers title + first 400 chars, so a reheaded wire story slips through; conversely two genuinely different articles that open with the same sentence collide. Both were observed and are accepted |

## Classification

| choice | pros | limitations |
| --- | --- | --- |
| **Rule/lexicon classifier as the shipped default** (`LLM_PROVIDER=rules`) | deterministic, free, offline, versionable (`rules-lex-v1` -> `v3` is measurable), and gives the demo a working end-to-end path with no API key | it reads words, not meaning: "Ford *denies* layoff reports" scores as Risk. Confidence is lexical density (`base + 0.13 * hits`, capped 0.9), which is comparable between runs but is **not** a probability. Every new vocabulary needs a code change, and multi-label judgement calls ("a recall that is also a regulation story") depend on hand-tuned term placement |
| **LLM multi-label classifier behind the same interface** (`provider.py`, any OpenAI-compatible endpoint) | handles negation, hedging and cross-category stories; the same `Verdict` validation applies; temperature 0 + JSON-object mode keeps output parseable | needs a key and a network, costs money per pass, and is non-deterministic enough that a re-run can change a verdict - which is why verdicts are stored per `prompt_version` and *model* rather than overwritten. No provider-specific SDK is used, so response-format drift is caught by `extract_json` rather than typed |
| **Curated-alias NER over a closed 17-company set** (instead of spaCy/transformers NER) | zero model download (spaCy's `en_core_web_trf` is ~1 GB), exact control over the watchlist, and it distinguishes `MAN Truck` from a sentence about a "man"; per-company non-overlapping span consumption keeps counts honest | it only knows the 17 companies in `data/companies.yaml`, so a story about IVECO or Sinotruk tags nothing - acceptable for a watchlist tool, misleading if presented as generic NER. Alias maintenance is manual and casing-sensitive |
| **Hand-labelled gold set (`data/gold_labels.json`, 23 rows) + own harness** | the metrics are reproducible without a network, include empty-label rows (so precision is not flattered), and `--model` makes versions comparable side by side | 23 samples is small: one relabelled row moves macro F1 by ~0.02. It is a *validation set*, not a benchmark, and it was labelled by one person (me) against the codebook, so inter-annotator agreement is unknown. The 4-category taxonomy itself is fixed by the brief and was not validated |

## Serving

| choice | pros | limitations |
| --- | --- | --- |
| **FastAPI** | the brief asked for Swagger; `/docs` and `/openapi.json` are free, Pydantic v2 gives validation at the boundary, and dependency-injected sessions keep route functions thin | `TestClient` now emits a Starlette deprecation warning about the httpx adapter; async route definitions would block on the sync ORM, so routes stay `def` and uvicorn runs them in a threadpool - which caps throughput at the threadpool size |
| **Streamlit** | the brief named it; a feed, filters, trends and search are ~200 lines; renders anywhere from `httpx` alone | it re-runs the whole script per widget, so a dashboard page with 6 queries re-queries the API on every click (partially mitigated with two `st.cache_data` wrappers and short TTLs); no real client-side state, no URL-shareable filter state; multi-session scaling means more processes, not more workers |
| **API-only dashboard** (the Streamlit app never imports the DB layer) | one source of truth; the dashboard is a deployable client of the same public contract | every filter change is a round trip; a bug in the API is a bug in the UI; the dashboard cannot show anything the API does not expose (that constraint is what forced `/companies/supply-map` to exist) |
| **APScheduler `BlockingScheduler`** (instead of Celery + Redis) | one process, no broker, no extra infra; `max_instances=1` + `coalesce` + `misfire_grace_time` already gives the "don't stack runs" behaviour that matters here | single-node only: no distributed workers, no retries with a dead-letter queue, no per-task alerting; if the process dies nothing notices. `session_scope()` per job means a crashed job leaves no transaction open, but also no rollback-and-retry semantics |
| **Optional `X-API-Key` middleware, no real auth** | enough for a demo behind a private network and it is one env var to turn on | it is a shared secret, not identity: no per-user visibility control, no rotation, no rate limit. Public deployment must sit behind a real gateway |

## Quality and operations

| choice | pros | limitations |
| --- | --- | --- |
| **`pytest` + `respx` + captured fixtures** | 57 tests, no network, no key, deterministic; live-payload shapes (EDGAR JSON, newsroom HTML) are frozen as fixtures so parser regressions fail the build | mocks drift from reality - the EDGAR parser was wrong for a day because the fixture was written from documentation, not from a captured response. A nightly live smoke test would catch that and does not exist |
| **`ingest_runs` ledger as the observability layer** | restart-safe and answerable with SQL ("what did stage X do at 03:00?"), no log scraping needed for the demo | counters only, no latency histogram and no alerting; the worker's own liveness signal is "the ledger grows" |
| **Structured `logging` to stdout** | one format for all three services, container-native | no shipping/aggregation; `logfmt`-ish text, not metrics |
| **Docker multi-stage image shared by three services** | one artifact, `COPY --from=deps` keeps the runtime layer slim, non-root user, healthchecks without `curl` | **not built or run on this host - Docker is not installed here.** The compose file was validated by parsing it and resolving the `x-app` anchor; the image itself is unverified |
| **`entrypoint.sh` migration/seed gate** | `docker compose up` works from empty; migrations retry 20 times while Postgres initialises | the seed runs only on the `api` service; if the seed step is skipped, the worker has no sources to poll and will look healthy while doing nothing |

## Deliverable-format choices

- **Mermaid diagram in `docs/ARCHITECTURE.md`** rather than a PNG: it lives in the
  repo, diffs reviewably, and renders on GitHub. Limitation: it is not a C4-quality
  diagram and needs a renderer.
- **`data/*.yaml` as the configuration-as-code layer** for the watchlist and the
  catalogue: a reviewer can audit "which companies, which sources" in one file and
  `python -m signals.cli seed` is idempotent. Limitation: no schema validation on
  the YAML beyond what the seeder assumes; a typo in a company name silently
  creates a new entity.
- **No frontend build, no TypeScript, no ORM-migration CI**: the brief is a data
  pipeline with a dashboard, and every one of those would have added a toolchain
  without adding a monitored company.
