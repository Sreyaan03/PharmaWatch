# Signal-detection reference sets

Two published reference standards of drug-outcome pairs, used to score PharmaWatch's signal
detection (`validation/signals_benchmark.py`). Each pair is labelled **1** (a positive control:
the drug is believed to cause the outcome) or **0** (a negative control: believed not to).

| file | set | pairs | positives | negatives | outcomes |
|---|---|---|---|---|---|
| `omop.csv` | OMOP reference set (Ryan et al. 2013) | 399 | 165 | 234 | 4 |
| `euadr.csv` | EU-ADR reference standard (Coloma et al. 2013) | 93 | 43 | 50 | 10 |

## Provenance

- Retrieved 2026-10-15 from the OHDSI `MethodEvaluation` R package, `data/omopReferenceSet.rda`
  and `data/euadrReferenceSet.rda` (https://github.com/OHDSI/MethodEvaluation, Apache-2.0).
- Converted once by `scripts/validation/convert_reference_sets.py`; drug names are lower-cased and
  outcomes mapped to short keys. The package's concept ids are kept as `omop_exposure_id` /
  `omop_outcome_id`. SHA-256 of the `.rda` files:
  - omopReferenceSet.rda `8d0964fce92c557d98cb751ec2aea7c1e3e1643696231a177128579a5662fa15`
  - euadrReferenceSet.rda `5cea251e7ab03a232f871abfb448655b8474ffab325bb19b4fbae8d139203c32`
- Original papers:
  - Ryan PB, Schuemie MJ, Welebob E, Duke J, Valentine S, Hartzema AG. Defining a reference set to
    support methodological research in drug safety. Drug Saf 2013;36 Suppl 1:S33-47.
    doi:10.1007/s40264-013-0097-8
  - Coloma PM, Avillach P, Salvo F, et al. A reference standard for evaluation of methods for drug
    safety signal detection using electronic healthcare record databases. Drug Saf 2013;36(1):13-23.
    doi:10.1007/s40264-012-0002-x

## Things to know before using these numbers

- **OMOP counts match the paper** (165 positive, 234 negative).
- **EU-ADR has 93 pairs here; the paper reports 94 (44 positive, 50 negative).** The package copy
  has 43 positives and no positive control for cardiac valve fibrosis. One positive is missing
  from this copy; it was not added from memory.
- **The OMOP negative controls are imperfect.** A published audit found that a substantial share
  (reported as about 17%) may be misclassified. Specificity measured here is therefore a lower
  bound on how the tool would behave against true negatives.
- Several drugs appear with both labels for different outcomes (36 in OMOP, 4 in EU-ADR).
- Drug names are generic or brand names as in the package. `drug_rxnorm_map.csv` records how each
  resolves through the identity service; drugs that do not resolve are listed, not dropped.
- The two sets share outcomes and some pairs. They are scored separately and are not pooled.

## Outcome definitions: DRAFT, not clinically reviewed

`outcome_pt_map.csv` lists the MedDRA Preferred Terms used to define each outcome in FAERS. It
was drafted by the assistant and **has not been reviewed by anyone with clinical or MedDRA
expertise**. Every row is `status: draft`. Results computed with it must be labelled draft until a
faculty reviewer changes the status and signs the change in this file:

    reviewed_by: ____________________   date: ____________

## Freeze

`CHECKSUMS.sha256` holds the digests of the committed files. `tests/test_signals_benchmark.py`
fails if any file changes without the checksum being updated in the same commit, so a change to
the reference data is always deliberate and visible.
