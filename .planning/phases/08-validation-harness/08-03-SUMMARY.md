# Summary 08-03: NER Benchmark, One Command, One Page, CI Gate

## What was built
- **NER benchmark** (`validation/ner_benchmark.py`): the deployed model scored on the ADE Corpus V2 relation annotations (4,271 sentences) per entity type, exact and overlap matching, plus a fragmentation measure and a comparison of the application's all-pairs rule with sentence-level pairing. The corpus is read from `C:\PharmaWatchData\reference\` (licence not stated; not committed).
- **`python -m validation.run_all [--smoke] [--only ...]`**: runs the signal, interaction and NER benchmarks, skips (and records) any whose inputs are missing, and writes `reports/validation/summary.json`.
- **Methods & Validation page** (`src/validation.js`, `/api/validation/summary`): renders only what the summary file contains, with reference set, data version, run date, intervals and a known-limitations list. Nothing on it is computed in the browser.
- **CI gate** (`validation/gate.py`, `baselines.json`, `tests/test_validation_gate.py`): fails when a smoke metric falls below baseline minus tolerance. Gated: deployed interaction model (committed splits), signal benchmark logic (known-answer database), and, where the data is loaded, real signal accuracy and NER. CI runs a smoke benchmark and uploads `reports/validation/`.
- **`docs/METHODS.md`** replaces five overlapping ML guides (deleted) and records every formula, threshold, data rule and limitation. `docs/INDEX.md` updated.

## Results
**NER (full corpus):** drug mentions exact F1 0.196, overlap F1 0.657; adverse-effect mentions exact 0.179, overlap 0.622. **63% of drug predictions and 58% of effect predictions are only a fragment of an entity** (`metformin` becomes "met"; `quetiapine` becomes "que" + "tia"). Drug-effect pairing F1: current all-pairs rule 0.012, sentence-level 0.027. Precision is a lower bound (ADE annotates only related entities). The model is `d4data/biomedical-ner-all`, not BioBERT; METHODS.md says so, and the remaining UI wording is a Phase 10 item.

**Signals (all 20 quarters, draft outcome map):** see 08-01 summary.

## Findings and fixes along the way
- **Identity defect**: exact RxNav matching replaced a fuzzy search that mapped sulfamethoxazole to sulfamethazine, scopolamine to methscopolamine, hyoscyamine to atropine.
- **Defaulted dates**: 19.4% of all FAERS event dates are exactly 1 January (year-only dates stored as YYYY0101; an even spread gives 0.27%). This created a false January spike in seasonality and would distort time to onset. Such dates are now incomplete for both. Sanity check afterwards: oseltamivir shows a winter peak (p 3e-17); warfarin INR events show none (p 0.11).
- The data version now includes an identity-build fingerprint so cached results from an older build are never served.

## Still open
- NER fragmentation is measured, not fixed (Phase 10).
- Outcome definitions for the signal benchmark are a draft awaiting clinical review.
- `is_harmful` alias to be removed in Phase 10.
- NER and full-signal gates run only where the corpus/FAERS data are present, not in CI.
