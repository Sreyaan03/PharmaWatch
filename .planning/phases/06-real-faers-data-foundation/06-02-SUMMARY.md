# Summary 06-02: Drug and Event Identity

## What was built
- **`clients/rxnav.py`**: RxNav (NLM RxNorm) client. Thread-safe, every response cached on disk (`<DATA_DIR>/rxnav_cache.jsonl`), request starts spaced to stay under NLM's 20/s limit, retries with backoff, `resolve_many` for concurrent lookup. Two calls per name (name -> RxCUI, RxCUI -> ingredient).
- **`faers/identity_build.py`**: adds `drug_identity`, `case_drug` (one row per case and ingredient, flagged suspect), `pt_dictionary` and `identity_build_info` to a loaded FAERS database. Combination products are split on the backslash and each part resolved. Reports coverage.
- **`services/identity.py`**: `IdentityService.resolve_drug` (loaded data first, then RxNav) and `resolve_event` (exact PT, reviewed synonym, or candidates).
- **`faers/event_synonyms.csv`**: 15 reviewed term -> PT mappings, each with its source and a note.
- **Watchlist**: five events that are not Preferred Terms replaced with real PTs; a test checks every watchlist event and synonym target against the loaded data.

## Decisions driven by evidence
- **Fuzzy matches are never accepted.** RxNav's approximate matcher resolved "metfromin" to **merbromin** (an antiseptic) with a score of 9.2, higher than the correct "ibuprofn" -> ibuprofen at 8.5. Scores do not separate right from wrong, so an approximate match is returned only as a `suggestion`, and unresolved names stay unresolved.
- **Warfarin's watchlist event is "Subdural haematoma"** (10 reports, PRR 79), not the planned "Haemorrhage" (3 reports, no signal). Typing "bleeding" still resolves to Haemorrhage through the synonym table, with a note that bleeding is spread over many PTs and needs grouped terms (a MedDRA licence).
- **Names with fewer than 10 reports are not resolved.** In 2026Q2, the top 3,000 names cover 99.0% of drug reports, so the long tail of misspellings costs time and adds nothing.

## Verification (2026Q2, 300 most-reported names)
| | |
|---|---|
| Names resolved | 295 of 300 (98.7% of drug-report pairs) |
| Unresolved | `vitamins`, `unspecified ingredient`, `minerals\vitamins`, `insulin nos`, `dietary supplement\ubidecarenone`: not specific ingredients, correctly left alone |
| Match type | 315 exact, 0 approximate |
| Time | 312 distinct names in 174 s with 8 concurrent requests |
| Watchlist | all 15 events are Preferred Terms present in the data |

89 tests pass (20 new for identity, 2 skipped without the real database).

## Findings
- Five of the 15 original watchlist events were not Preferred Terms (Bleeding, Nephrotoxicity, QT Prolongation, Peripheral Neuropathy, Peripheral Edema), as the audit predicted.
- `src/index.html` shows a hard-coded top-events list with fixed counts (for example "QT Prolongation 112,900"). It is an invented number in the static page; it is handled with the dashboard cleanup in Phases 7 and 10.
- RxNav latency is about 1 s per call and parallelises well (8 concurrent calls took 2 s), so a full run is minutes, not hours.

## Not done
- The full identity build (about 3,200 names at the 10-report threshold per quarter, more across 20) is run once all quarters are loaded, in plan 06-03.
