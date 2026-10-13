# Summary 07-01: Real Statistics (onset, strata, signal classes)

## What was built
- **`faers.disproportionality.compute_metrics(a,b,c,d)`**: the one place PRR, ROR (log-normal 95% CI), Yates chi-square and IC/IC025 are computed. Zero cells use the Haldane-Anscombe 0.5 correction; an undefined table returns zeros with `defined: false`. Classes are `none`, `signal` (3+ cases, PRR >= 2, chi-square >= 4) and `strong_signal` (also ROR lower bound > 1 and IC025 > 0), each with the per-criterion results.
- **`faers.stats`**: `time_to_onset` (real therapy-start to event-date difference; incomplete dates counted by reason, no summary below 10 usable cases), `stratified_metrics` with `SEX_STRATA` / `AGE_STRATA` (each stratum has its own 2x2 table), `unclassified_cases`, `reports_by_country`.
- **Services**: demographics computes a PRR per sex and per age group; a group is named higher-risk only when its PRR interval lies entirely above the others'. Country figures are a share of reports, labelled as reporting volume, not risk. Time to onset is returned as `no_data` with the reason when fewer than 10 reports are usable.
- **Dashboard**: top reported events come from the loaded data (product-use terms excluded and listed in the response).
- **Caches** store the data and algorithm version; a payload from other data or older code is a miss.
- **Frontend**: signal classes renamed everywhere; invented alerts, counters and "live" pills removed; header status polls the backend.

## Verification
- Golden tests: 8 contingency tables with independently computed expected values (`test_disproportionality.py`); onset, strata, country, cache-version and endpoint tests on data built so the right answers are known (`test_faers_stats.py`, 17 tests).
- A test asserts the old `share x 3.2` "PRR" can no longer be produced.
- Route snapshot changed only by the new `/api/dashboard/top-events` route and `share_pct` on geo.

## Deviations
- `/api/prr`'s `is_signal` now follows the class (anything other than `none`) instead of its own PRR test. The boxed-warning service keeps its own test until 07-03.
- Seasonality and velocity still match drugs by text; they are not on the ingredient path yet.
