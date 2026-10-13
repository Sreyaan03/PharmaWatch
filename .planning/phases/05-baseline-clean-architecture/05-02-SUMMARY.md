# Summary 05-02: One Copy of Each Helper

## What was built
| Was duplicated | Now |
|---|---|
| Admin-key check, 4 copies in route handlers | `core/auth.py` `require_admin` decorator (same 403/401 responses and messages) |
| `get_cached_payload` / `set_cached_payload` in two services | `core/cache.py` `PayloadCache(table, kind_column, label)`; the two services bind it to `temporal_cache` and `demographic_cache` |
| Z-score spike loop in two places | `faers/stats.py` `zscore_spikes(counts, window, min_history, threshold)` |
| Two-key HBase pair lookup in 3 places | `clients/hbase.py` `find_pair_row(a, b)` |
| Private `PatchedRequests` in the forecaster | uses `clients/http.py`; the unused MySQL `fda_cache` read/write helpers were removed from `database.py` |
| `/api/analytics/prr` re-implementing the PRR path | calls `services.signals.compute_disproportionality_helper(..., source="duckdb")` |
| Co-reported-drug request repeated in two graph builders | `_co_reported_drugs()` |
| `/` registered in `app.py` and `general.py` | the unreachable copy in `general.py` removed (`app.py`'s copy, which is rate-limit exempt, was the one serving) |
| `print` diagnostics in request-time code | `logging` (37 call sites); `app.py` configures the root logger |

## Verification
- 37 tests pass (28 + 9 new: `test_hbase_lookup`, `test_zscore_spikes`).
- Differential harness: 76/76 requests identical to the pre-refactor baseline in status, body and SQL log.
- Route snapshot differs only by the removal of the duplicate `/` rule, reviewed and accepted.
- Acceptance: `Admin endpoints disabled`, `np.std` and `class PatchedRequests` each occur once in `backend/`.

## Deviations and decisions
- `PayloadCache` is a small class rather than two free functions, so each service keeps its existing call sites.
- `/api/graph` and `/api/graph/detailed` share only the openFDA co-report request, not one function with a flag: they differ in drug casing, grouping rule and node attributes, and merging them would have changed output.
- The forecaster's HTTP path changes in two ways, both intended: the request URL no longer gets a literal `api_key=None` when no key is set, and its responses are cached by `requests-cache` (24 h) instead of the MySQL `fda_cache` table (7 days). The `fda_cache` table itself stays until the Phase 11 migration (a test lists it).
- Only request-time diagnostics were converted to logging. Command-line tools (loaders, parsers, benchmarks) keep `print`, where progress output is the point.
- Unused imports that `ruff --fix` removed from files outside this plan were reverted to keep the diff scoped.

## Left for 05-03
Dead-file removal, the HBase start endpoint, Docker/CI path updates, `sys.path` edits in `app.py`, and the architecture test.
