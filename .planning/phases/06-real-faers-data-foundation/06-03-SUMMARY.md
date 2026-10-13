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

## Still to do (needs the FDA host)
`fis.fda.gov` has been resetting my connections since a burst of probes early in this session (`api.fda.gov` and RxNav are fine). Only 2026Q2 is on disk. When it answers again, from `backend/`:

    set DATA_DIR=C:\PharmaWatchData
    python -m faers.downloader --latest 20     # skips verified files, resumes partial ones
    python -m faers.parser --all
    python -m faers.loader
    python -m faers.identity_build             # about 30 minutes; cached lookups are reused

then add `DATA_DIR=C:\PharmaWatchData` to `backend/.env`, run `python -m pytest backend/tests/test_identity.py` (the two real-data tests then run), and record the spot checks (warfarin, clozapine, metformin) in this summary.
