# Summary 06-03: Single Statistics Path (code complete; 20-quarter load pending)

## What was built
- **`faers/stats.contingency()`**: the one function that produces a 2x2 table. Counts distinct de-duplicated cases; a drug is exposure only when primary or secondary suspect; a combination must have every ingredient suspect in the same case; event match is case-insensitive; an optional `where` restricts the cohort (used for sex and age strata in Phase 7).
- **`case_drug`** table (per case and ingredient, suspect flag) built by `faers.identity_build`, so a count is a join on RxNorm ingredient instead of a regular expression on a free-text name.
- **`services/signals`**: `/api/prr`, `/api/analytics/prr`, `/api/signals`, `/api/prr-trials` and the NER-mining scorer all go through `compute_disproportionality_helper`, which resolves the drug and event through the identity service, then calls `contingency`. A name that cannot be resolved returns zero counts with an `unresolved` list and candidates; it is never matched loosely.
- **Labelled fallback**: the openFDA path now returns `fallback: true` and a note, and restricts the drug to suspect (`drugcharacterization:1`).
- **Versioned cache**: `signals_cache.data_version`; a cached row is reused only while it matches the loaded data's version, and unversioned rows are recomputed. Migration is additive and idempotent (`ADDITIVE_COLUMNS`); the writer tolerates a database that has not been migrated.
- **Containers** get `DATA_DIR=/app/data` explicitly, so a Windows path in `backend/.env` cannot leak into them.

## Verification
- 104 tests pass (15 new in `test_single_stats_path`, plus identity tests). The key one asserts the same pair gives identical a, b, c, d from `/api/prr`, `/api/analytics/prr`, `/api/signals` and `/api/prr-trials`, and that brand, case and synonym inputs reach the same counts.
- Concomitant drugs do not count as exposure; the superseded version of a case does not count; combination products require all ingredients; strata restrict every cell.
- Differential harness against the Phase 5 baseline: 71/76 identical. The 5 differences are intended: the labelled openFDA fallback (1) and the corrected watchlist with `data_version` (4 `/api/signals` variants).
- Route snapshot changed only by the new `fallback` and `note` keys on `/api/prr`.
- Migration applied to the local dev MySQL (`signals_cache.data_version` added; legacy seed skipped).

## Findings
- A plain generic name ("metformin") was unresolved when the data only held salt forms ("metformin hydrochloride"). Fixed: lookup also matches the resolved ingredient names, so generics work offline.
- `/api/prr` dropped the identity block because its response lists fields explicitly. It now passes `identity`, `unresolved`, `fallback` and `note` through when present.

## Deviations
- Only the **2x2 count path** was moved to the ingredient join in this plan. The temporal, demographic, time-to-onset and top-reaction functions still match drug names by text; Phase 7 rewrites them (real onset, stratified PRR) on top of `contingency`.
- The fallback keeps `source: "openfda"` and adds `fallback: true`, instead of renaming the source, so existing frontend badges keep working.

## 20-quarter load (completed 2026-10-17)
The FDA zips were downloaded manually into `C:\PharmaWatchDataaers_raw\` and registered with `--adopt`. All 20 quarters (2021Q3-2026Q2) were parsed and loaded: 8,474,526 reports, 7,222,094 cases kept after de-duplication (914 MB). Drug identity: 6,630 of 13,487 names resolved directly, covering 97.0% of drug-report pairs; 20.6 million case-ingredient rows.

Two defects were found and fixed while doing this (see the 08-01 and 08-03 summaries): the RxNav lookup took the first of several similarly named concepts (sulfamethoxazole mapped to sulfamethazine), and 19% of event dates are FAERS year-only defaults (YYYY0101).

Spot checks on the full data (a, b, c = reports with the drug and event, with the drug only, with the event only):
| pair | a | b | c | PRR |
|---|---|---|---|---|
| warfarin + haemorrhage | 357 | 6,012 | 33,793 | 12.0 |
| clozapine + agranulocytosis | 683 | 51,932 | 6,246 | 14.9 |
| metformin + lactic acidosis | 8,259 | 36,296 | 3,910 | 340.3 |
| atorvastatin + rhabdomyolysis | 1,682 | 32,344 | 9,649 | 36.8 |
| metformin + hepatic failure | 103 | 44,452 | 7,492 | 2.2 |
The four known associations are strong; the unrelated pair is weak. `DATA_DIR=C:\PharmaWatchData` still needs to be added to `backend/.env` so the app finds the data without it being set in the shell.
