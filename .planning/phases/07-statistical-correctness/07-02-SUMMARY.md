# Summary 07-02: No Invented Numbers, Provenance On Every Result

## What was built
- **`core/response.with_meta`** and an `after_request` hook: every JSON object from an `/api/` route gets `meta` = `evidence`, `data_version`, `model_version`, `generated_at`. Existing keys are untouched.
- **`services/provenance.py`**: the table of path prefixes to evidence kind. Kinds: `measured` (counted from reports), `modelled` (a model's prediction), `retrieved` (literature, trial registry), `operational` (deployment state). The plan named three kinds; `retrieved` and `operational` were added because calling PubMed results or queue depth "measured" or "simulated" would be wrong. FAERS-backed results carry the loaded quarters as `data_version`; boxed-warning results say "openFDA (live query)".
- **Dashboard**: `reports_analyzed` is the registry count or `null` (the `signals x 1200` estimate is gone); `signals_detected` counts only pairs that are signals; the sine-curve distribution of hourly signals is gone (no recent rows gives empty series and `source: no_data`); the series are `strong_signal` / `signal` / `none` and the chart says it plots when pairs were computed.
- **Seasonality**: chi-square test against a spread proportional to days in the month, alpha 0.01, not applicable below 60 cases; months are named only when the test is significant. On the real 2026Q2 data, a 10-case pair that the old rule would have called seasonal now gets no verdict.
- **Velocity**: the newest calendar quarter in the series is excluded (reports for recent events are still arriving) and listed in `incomplete_months_excluded`.
- **Frontend**: `randomArray`, `generateRandomArray`, the cycling "4.2 TB stored" HDFS figure and the simulated NER throughput are removed; they show "Illustrative (not connected)" and "BioBERT not running". `Math.random` remains only in `distributed_storage.js`, which is labelled as the architecture demonstration.

## Verification
- `test_no_invented_numbers.py` (37 tests): with MySQL unreachable, no FAERS data and the network answering `{}`, each measured endpoint returns only zeros/nulls (the one non-zero, `/api/signals` `total`, is asserted equal to its list length); every result has `meta`; every `/api/` route has an evidence kind, so a new route cannot ship without one; seasonality and tail-dropping unit tests.
- Full suite: 187 passed, 2 skipped. CI lint rules clean. Route snapshot changed only by `meta` on analytic routes and the renamed dashboard keys.

## Deviations
- The `meta` hook sits in `app.py` driven by a path table rather than being called in each of the ~45 routes; one place, enforced by a test.
- The category chart still classifies drugs by a hand-written keyword list; it is a grouping, not a measurement, and is left for review in 07-03/Phase 11.
