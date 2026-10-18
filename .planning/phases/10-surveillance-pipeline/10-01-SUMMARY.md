# 10-01 Summary: Kafka cleanup, release trigger, linked evidence

## Done
- **No statistics in the stream.** Removed `handle_faers`, `compute_prr` and the `faers-raw` topic (consumer), `poll_faers` (producer), the hard-coded `INITIAL_GLOBAL_BACKGROUND` / `TOTAL_FAERS_POPULATION` / `get_global_event_count`, and `scripts/recalculate_signals.py`. A test fails if `ingest/` imports any statistics code or if any of those names come back.
- **Schema.** `drug_events_agg` and `faers_background_events` removed. The migration drops them if present and deletes any `kafka_faers` rows; on the local database that removed 40 hard-coded background rows (no stream-computed signals were stored). New tables `faers_releases` and `evidence_links`. `kafka_pipeline_stats` gained `partition_id` and a unique key on (topic, partition, offset). The duplicate-removal step uses a grouped delete; the first version (a self-join) timed out on 24,527 rows and was replaced.
- **Release trigger.** The producer checks once a day for a FAERS quarter newer than the loaded data and publishes `faers-release`. The consumer records it. With `FAERS_AUTO_REFRESH` on it starts a `faers_refresh` job (download, parse, reload, identity rebuild in `faers/pipeline.py`); otherwise an admin starts it with `POST /api/admin/faers/refresh`. A message for a loaded quarter does nothing. The dashboard shows a notice when a newer quarter is announced (`GET /api/faers/releases`).
- **Linked evidence.** `faers/mentions.py` finds drug ingredients (identity table, ingredient names, brand map) and MedDRA terms in titles, matching whole words exactly. On the real database: 8,631 drug names, 7,825 terms. Papers link to every drug and term in the title; trials link only to their drug (titles name the condition treated); recalls link to drugs in the product text. `GET /api/evidence?drug=&pt=` returns the items, and the signal detail shows them under "External evidence", separate from the statistics.
- **Identity fix found on the way.** `IdentityService.resolve_drug` did not use the brand map, so "Glucophage" did not resolve offline anywhere in the app. It does now.
- **Idempotency.** Every write is an upsert on a natural key. Test against MySQL: all four message types delivered twice leaves `evidence_links`, the three Kafka tables, `faers_releases` and `kafka_pipeline_stats` byte-identical. It passed locally against MySQL, and CI runs it after its migration step.
- **Compose.** The web app no longer waits for Kafka (dev and prod). Producer and consumer mount the FAERS database read-only. The Kafka image gains duckdb and pyarrow.

## Checks
- Full test suite, ruff (CI rule set), route inventory refreshed (three new routes), provenance registered for them.
- Live check through the app on real data: a paper and a recall naming metformin both appear for "Glucophage"; the paper also lists "Lactic acidosis" and "Chronic kidney disease"; the refresh route returns 401 without the admin key.

## Limits
- Matching is by name only. A paper that names a drug is not necessarily about its safety, and a term in a title may be an indication. The UI says "found by name ... not reviewed".
- Recalls (openFDA enforcement) are quality defects, not adverse-event evidence; they are labelled "FDA recall".
- Auto-refresh inside the consumer container needs the data folder mounted read-write; the default mount is read-only and auto-refresh is off.
- The consumer loads its mention index once; after a refresh it picks up new names on restart.
- The running `kafka-producer` container image predates this change. It must be rebuilt (`docker compose build kafka-producer kafka-consumer`) or it keeps the old behaviour.
- The Pipeline page's simulation code still exists (10-07 removes it); its topic label was renamed from `faers-raw` to `faers-release`.
