# Summary 08-02: Interaction Model Retrained, Evaluated Honestly, Deployed by Manifest

## What was wrong
The Phase 4 model took **one optimiser step per epoch** (full batch) and was reported as converged; "ablations" were an untrained network, a dot-product stand-in and a zeroed-edge inference run; calibration was fitted and scored on the same pairs and had made ECE worse (0.0768 to 0.0780) while the API still said `calibrated: true`; the training negatives could include held-out positives; baselines used a 30k subsample.

## What was built
- **Training** (`scripts/train_gnn_model.py`): real mini-batches of pairs (93 optimiser steps per epoch, logged), negatives that exclude every known positive including the held-out S0 positives, validation on a selection half only, a wall-clock budget per run, and four separately trained variants (`full`, `no_fingerprint`, `no_edge_features`, `concat_head`).
- **Splits** (`scripts/curate_ddi_dataset.py --migrate-splits`): validation pairs split by drug into a selection half (26,206 pairs, 85 drugs) and a calibration half (25,844 pairs, 85 drugs); SMILES columns dropped from the CSVs.
- **Baselines** fitted on the full training split (190,392 pairs), same negative policy.
- **Evaluation** (`validation/ddi_eval.py`): AUROC, AUPRC and precision@k (Recall@k removed); GINE variants reported as mean ± std over trained seeds; no row comes from an untrained model; findings claim only differences beyond the seed spread.
- **Calibration** (`validation/ddi_calibrate.py`): temperature scaling and isotonic regression compared by cross-fitting on the calibration half; ECE reported on the test sets; `calibrated` true only if ECE improved on S1.
- **Deployment**: `trained_models/ddi_manifest.json` names the model, version, calibrator, threshold and the calibration scope; `ml/ddi/inference.py` serves whatever the manifest says (weights-only load). API adds `predicted_interaction`, `calibrated`, `model_version`; `is_harmful` kept as a deprecated alias (remove in Phase 10).

## Results (test sets; 1:1 sampled negatives, base rate 0.5)
| Model | S0 AUPRC | S1 AUPRC | S2 AUPRC |
|---|---|---|---|
| GINE full (5 seeds) | 0.979 ± 0.003 | **0.851 ± 0.005** | 0.725 ± 0.009 |
| XGBoost | 0.831 | 0.753 | 0.660 |
| Logistic regression | 0.873 | 0.641 | 0.554 |
| NN-Tanimoto | 0.787 | 0.731 | 0.584 |

Ablations (S1 AUPRC, 3 seeds each): no fingerprint 0.770 (clearly lower); no edge features 0.843 and concatenation head 0.847 (both within seed spread of the full model: no benefit demonstrated). Each run was capped at 15 minutes (2-3 epochs); longer training was not tried.

Deployed: GINE full, seed 42 (selection AUPRC 0.830). Isotonic calibration: ECE 0.153 to 0.021 (S1) and 0.265 to 0.079 (S2), but **0.007 to 0.149 for S0**; the manifest records that the score is calibrated only for pairs with an unseen drug. Threshold for "predicted interaction" 0.607, chosen on the calibration half for precision 0.80 (0.819 on the S1 test set).

## Deviations
- NN-Tanimoto is reported but cannot be deployed (needs the training positives at serve time); it was not the best candidate.
- Per-seed checkpoints are git-ignored (reproducible); the deployed one is copied to the tracked `ddi_deployed.pth`.
- The README's old ablation claims ("fingerprints indispensable", "drops to random chance") were removed.
