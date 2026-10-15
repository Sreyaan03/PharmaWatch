# Summary 08-01: Signal Benchmark (runner complete; numbers preliminary)

## What was built
- **Reference sets** (`backend/validation/reference/`): OMOP (399 pairs: 165 positive, 234 negative, matches Ryan 2013) and EU-ADR (93 pairs: 43 positive, 50 negative) from the OHDSI MethodEvaluation package, converted once by `scripts/validation/convert_reference_sets.py`. README records source, licence, retrieval date, `.rda` checksums and the paper's citation. A checksum freeze (`CHECKSUMS.sha256`) and a test fail if any file changes silently.
- **Drug map**: all reference drugs resolved through the identity service; 231 of 232 resolve (`endopeptidases`, an enzyme class, does not).
- **Outcome map** (`outcome_pt_map.csv`): MedDRA Preferred Terms per outcome. **Every row is `draft`: drafted by the assistant, not reviewed by anyone clinical.** The results file carries this status.
- **`validation/metrics.py`**: AUC, sensitivity, specificity, precision@k, bootstrap intervals; AUC verified against scikit-learn including ties.
- **`validation/signals_benchmark.py`**: scores every pair by PRR, ROR lower bound and IC025 and by the app's class rule; reports coverage (pairs with 3+ cases) and scores both "covered pairs only" and "all pairs, uncovered counted as no signal"; one pass over the data for all pairs (5 s on one quarter); writes `reports/validation/signals.json`. `--smoke` writes a small file for CI.
- `contingency` now accepts a list of terms (an outcome's definition).

## A real defect found and fixed on the way (Phase 6 identity)
Resolving the reference drugs exposed wrong identities in the loaded data: `sulfamethoxazole` mapped to sulfamethazine, `scopolamine` to methscopolamine, `hyoscyamine` to atropine, `urea` to a urea+lactate combination. Cause: the RxNav lookup used the normalised search (`search=1`), which returns every similarly named concept, and the first was taken. Fixed: exact search first; the normalised search only as a fallback and only when it names one concept; several concepts must lead to the same ingredient. Identity tables were rebuilt (97.55% of drug-report pairs resolved, from 97.66%; 76 names changed ingredient). Regression tests added. The data version now includes an identity-build fingerprint, so cached results from the earlier build are not reused. Backup of the database before the fix: `C:\PharmaWatchData\backups\pharmawatch_faers_before_exact_rxnav_fix.duckdb`.

## Results so far: ONE quarter (2026Q2), draft outcome map. Not publishable.
| set | pairs | covered (3+ cases) | AUC(PRR), covered only | AUC(PRR), all pairs | signal sens / spec, all pairs |
|---|---|---|---|---|---|
| OMOP | 399 | 87 (22%) | 0.76 [0.60-0.89] | 0.65 [0.61-0.70] | 0.35 / 0.96 |
| EU-ADR | 93 | 49 (53%) | 0.74 [0.57-0.89] | 0.78 [0.69-0.87] | 0.72 / 0.84 |

Among covered pairs the app's `signal` rule has sensitivity 0.89 and specificity 0.61 (OMOP): it flags about 4 in 10 negative controls. IC025 scored slightly better than PRR (0.82 vs 0.76 covered). Coverage is the main limit: with one quarter most reference pairs have fewer than 3 cases.

## Still open for 08-01
- Load the 20 quarters and rerun; record the results as they come out.
- A faculty reviewer must review `outcome_pt_map.csv` and sign the README before any number is presented as validated.
- The EU-ADR copy lacks one positive control the paper has; no source for it was found.
- CI smoke job (`--smoke` on the fixture) is wired in 08-03.
