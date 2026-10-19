# Progress log

The brief defines a weekly cadence. This build ran as one continuous
implementation on **2026-10-19**, so "Week" below is the spec's deliverable
numbering, not elapsed calendar time - each section is the artefact, its status,
and what actually went wrong while producing it.

| Week | Deliverable | Status | Artefact |
| --- | --- | --- | --- |
| 1 | Company + Tier-1 mapping sheet | done | `data/companies.yaml` (5 OEMs, 12 vendors, 25 supply links), `/companies/supply-map` |
| 1 | Source catalogue | done | `data/sources.yaml` (29 rows, 26 enabled), `signals.tools.probe_sources` |
| 2 | Database schema | done | `src/signals/db/models.py`, `alembic/versions/0001_baseline.py` |
| 2 | 500-1000 raw records | done | 994 articles in `var/signals.db` |
| 3 | ETL + NER | done | `signals.etl.runner` (clean, enrich, re-tag), `signals.etl.entities` |
| 4 | Multi-label classification + confidence | done | `signals.classify` (rules + LLM provider), `event_labels` |
| 4 | Accuracy evaluation on a manual validation set | done | `signals.eval.harness`, `data/gold_labels.json`, macro F1 0.837 |
| 5 | REST API with pagination/filtering + Swagger | done | `signals.api.app`, 12 routes, `/docs` |
| 6 | Dashboard: feed, filters, trends, search, highlights | done | `src/signals/web/app.py` |
| 7 | Scheduled collection, restart-safe | done | `signals.worker`, `ingest_runs` ledger |
| 8 | Containerised deployment | done, **not executed** | `Dockerfile`, `docker-compose.yml`, `docker/entrypoint.sh` - Docker is not installed on this host |
| 9 | Tests | done | 57 tests, no network, no API key |
| 10 | Final report + architecture diagram + justified stack | done | `docs/ARCHITECTURE.md`, `docs/JUSTIFICATION.md`, this log |
| 10 | Public Git repository | **awaiting approval** | the working tree is not a git repository yet; `git init` + per-step commits need the owner's go-ahead |

## What broke, in order

Recording the failures is the useful part; each one became a test or an invariant.

1. **Verdict labels never normalised.** The LLM-facing schema computed a canonical
   key but did not write it back, so `"Technology & Innovation"` and `"regulation"`
   were emitted as-is and dropped by the category filter. Fixed in
   `classify/schema.py` (`canonical_category` + write-back + strongest-score-per-
   category dedupe) with a probe test.
2. **The entity tagger could not see acronyms.** `Company.match_terms` lowercased
   every alias, so `ZF` matched inside lowercase prose and `zf` never matched as a
   brand. Rewritten to preserve casing, with case-sensitive word-boundary matching
   for aliases of five characters or fewer.
3. **Mention counts were wrong.** The tagger collapsed to one alias per company and
   double-counted overlapping spans. Now spans are consumed per company and counts
   are totals across all aliases.
4. **Substring matching poisoned the classifier.** `"Traton reports second-quarter
   sales rose 4%"` became Compliance & Regulation because `"sec"` is inside
   `second-quarter`. Whole-word patterns fixed that, then broke plurals
   (macro F1 fell to 0.589), which is why the patterns carry light morphology
   `(?:s|es|ed|d|ing)?` and the lexicon was pruned of junk terms (`orders`,
   `million`, `billion`, bare `supply chain`). Net: macro F1 0.589 -> **0.837**.
5. **Macro F1 was a lie for a perfect classifier.** Averaging over all four
   categories scored a classifier that never predicted an unsupported category as
   0.5. The harness now skips categories with no support and no predictions, and
   reports `macro_over_categories` so the number is interpretable.
6. **`event_labels` triple-counted.** Re-running classification with new model
   versions kept inserting labels, so the dashboard showed 777 -> 623 -> 461 rows of
   mixed versions. `classifications` stayed append-only, `refresh_event_labels` now
   rebuilds the flat index from the best verdict per article, and 1864 superseded
   verdicts were pruned.
7. **187 corrupt SEC rows.** The EDGAR parser guessed JSON keys from documentation
   instead of a captured response and produced "SEC filer filing filing". Root
   causes: no CIK scoping (free-text search returned Clorox and floorplan-trust
   filings under a "Ford" source), no date window (results reached back to 2001),
   and a payload shape read from memory. Rows purged with their children, sources
   re-seeded with CIKs from `company_tickers.json`, 180-day window added to the
   request, parser rewritten against the live shape, and `tests/test_parsers.py`
   now asserts that shape.
8. **Real 2010-2014 articles were rewritten as today's news.** A well-meaning
   plausibility guard clamped any `published_at` older than 2015 to the fetch time.
   Seven genuine articles had their dates restored, `MIN_PLAUSIBLE` moved to 1990,
   and the policy is now encoded in `test_timestamp_policy_keeps_old_but_invents_nothing`.
9. **The API saw no rows inside its own test.** The pipeline test asserted
   `/events?category=risk` returned results while the stage's transaction was still
   open - the test client reads on a separate connection. Clarified as an invariant:
   stages `flush`, callers `commit`.
10. **Newsroom sources are not scrapable as configured.** `probe_sources` returned
    0 items for Daimler Truck and Volvo Group (release lists render client-side; the
    static HTML only exposes navigation anchors) and HTTP 403 for ZF's press portal.
    They stay disabled with that evidence recorded in `data/sources.yaml`, and their
    releases are still covered through Google News RSS.
11. **Title-only rows were being classified as if they were articles.** EDGAR and
    newsroom parsers store a headline and set `needs_enrichment`, but nothing
    consumed the flag - `classifications` were being written from 13-word rows. Added
    `enrich_bodies` (bounded per pass, retries on failure, restores the date the page
    declares) and made `run_etl` re-tag enriched rows, because a company that only
    appears in the body was otherwise invisible to every filter.

12. **"Is the date filter broken?"** In the Oct 5-20 window `/events` reports 74
    rows, 25 of them for the five OEMs, but only **6** once the four categories are
    selected - because 19 of those verdicts carry no label at all (the lexicon found
    nothing above its 0.2 floor). Verified at the SQL level and documented in
    `docs/ARCHITECTURE.md` section 5 rather than "fixed", since a stored negative is
    exactly what makes precision measurable.

## Next

1. `git init`, commit per step, and publish the repository (the one remaining
   deliverable).
2. Add a live-egress smoke test that runs nightly against the real feeds, so a
   parser drift is caught by CI rather than by the ledger going quiet.
3. Label ~60 more gold rows to make the metrics stable enough to compare an LLM
   classifier against the rules baseline with any confidence.
4. Run `docker compose up` on a host with Docker and record the output; the image
   is currently statically validated only.
5. Revisit the enrichment policy for EDGAR: fetching the filing's primary document
   instead of the index page would give the classifier real text.
