# Summary 06-01: Real FAERS Ingest

## What was built
- **`faers/downloader.py`**: fetches real FDA quarterly zips from `fis.fda.gov/content/Exports/` into `<DATA_DIR>/faers_raw`. Resumes partial files, checks size against the server's total, tests the zip, writes a SHA-256 sidecar. `--latest N` resolves the N newest released quarters; `--dry-run` lists without downloading.
- **`faers/parser.py`**: streams each member through pyarrow (bounded memory), keeps the full column set (`caseid`, `caseversion`, `age_cod`, `prod_ai`, `i_f_code`, `fda_dt`, therapy and outcome tables, the DELETE list), counts malformed rows, writes Parquet plus a manifest. Refuses files missing members or with fewer than 50,000 reports unless marked as a fixture.
- **`faers/dedupe.py`**: one row per case at its latest version (FDA rule: latest `fda_dt`, then largest `primaryid`), cases on any DELETE list dropped in every version, `age_years` from the reported unit (YR, DEC, MON, WK, DY, HR; NULL if the unit is missing or the result is outside 0-120), child tables restricted to the surviving version.
- **`faers/registry.py`**: `faers_quarters` table and `data_version()`.
- **`faers/loader.py`**: builds beside the live file and swaps in atomically; refuses to run when nothing was parsed.
- **`core/config.data_dir()`**: `DATA_DIR` environment variable, falling back to `<repo>/data`.
- **`tests/fixtures/faers/make_fixture.py`**: FDA-layout zip generator (the only FAERS-shaped data generator; the mock generator in the production downloader is gone).
- **Removed**: `faers/fetch_real_openfda.py`, the API sampler that wrote to the live database.

## Verification on a real quarter (2026Q2, 63.2 MB)
| Check | Result |
|---|---|
| Parser vs an independent profile of the same zip | 422,459 reports, 1,627,225 drug rows, 4,217 deleted case ids: identical |
| Cases kept | 422,458 (one duplicate case id collapsed) |
| Parse time / load time | 9.7 s / 5.1 s |
| PRR on the existing code | metformin + lactic acidosis 378; clozapine + agranulocytosis 6.3; ibuprofen + nausea 1.6 (no signal) |
| Query time over 422k cases, no indexes | 0.1 - 1.1 s |

69 tests pass (42 + 27 new). Differential harness 76/76 identical with `DATA_DIR` unset.

## Findings
- **The URL in the old downloader returned HTTP 400.** Real FAERS data had never been loaded; the project ran on mock files and a 3,800-report API sample.
- **27% of 2026Q2 reports are follow-ups** (`i_f_code = F`): cross-quarter de-duplication changes counts materially.
- **Only 42% of reports have an `event_dt`**, and therapy start dates are 8, 6 or 4 digits: time-to-onset (Phase 7) will rest on a minority of cases and must say so.
- **37% of reports have no age.**
- **`prod_ai` (active ingredient) is present on 98% of drug rows**: a far better identity key than the free-text drug name.
- **Warfarin + "haemorrhage" has only 3 reports**; bleeding is reported under more specific terms. Plan 06-02's event identity must handle term groups, not just exact terms.
- **The FDA host rate-limits bursts** (connection reset after about 30 probes in a few minutes). The downloader was redesigned to issue one request per new file, none for verified files, and to retry with backoff.

## Incident
While testing, the **old** `loader.py` ran by mistake (a shell redirect refused to overwrite the new file) and rebuilt `data/pharmawatch_faers.duckdb` from the mock Parquet files, replacing the 3,800-report sample with 100,000 mock reports. It was restored from the copy inside the `docker-pharmawatch:latest` image (same 3,800 / 3,079 / 2,749 rows, same Aug 24 date) and verified by the differential harness. The new loader cannot cause this: it reads manifests first and fails before touching any database if none exist.

## Not done in this plan
- 19 of the 20 quarters are not yet downloaded; only 2026Q2 is on disk. The FDA host was refusing connections when this was written. Run `python -m faers.downloader --latest 20` from `backend/` with `DATA_DIR` set; it skips what is verified and resumes what is partial.
- `DATA_DIR` is not yet in `backend/.env` and not yet set for the containers; both belong to plan 06-03 so the running app switches data sources once, after the full load.
- `analytics.py` age queries still treat the raw age as years; they move to `age_years` in Phase 7.
