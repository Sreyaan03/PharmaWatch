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

## Results: all 20 quarters (2021Q3-2026Q2, 7.2 million cases), draft outcome map
Signals are scored by the application's own rule (`signal`: 3+ cases, PRR >= 2, chi-square >= 4). Intervals are 95% bootstraps over pairs.

| set | pairs with 3+ cases | AUC (PRR), covered only | AUC (IC025), covered only | AUC (PRR), all pairs | signal sens / spec, covered only |
|---|---|---|---|---|---|
| OMOP | 226 of 399 (57%) | 0.796 [0.732-0.850] | 0.822 [0.766-0.873] | 0.712 [0.653-0.765] | 0.78 / 0.75 |
| EU-ADR | 77 of 93 (83%) | 0.884 [0.801-0.955] | 0.900 [0.823-0.967] | 0.838 [0.740-0.918] | 0.85 / 0.79 |

For comparison, one quarter gave OMOP coverage 22% (AUC 0.76) and EU-ADR 53% (0.74). More data raised coverage and tightened the intervals; discrimination improved modestly. Across both sets IC025 separated positives from negatives slightly better than PRR. The `signal` rule flags about 25% of negative controls among covered pairs (specificity 0.75-0.79), so a signal is a screening flag, not a finding. Recall counting uncovered pairs as "no signal": 0.55 (OMOP), 0.77 (EU-ADR). These are scored with a draft outcome map and imperfect OMOP negatives.

## Still open for 08-01
- A faculty reviewer must review `outcome_pt_map.csv` and sign the README before any number is presented as validated.
- The EU-ADR copy lacks one positive control the paper has; no source for it was found.
- CI smoke job (`--smoke` on the fixture) is wired in 08-03.
