# Phase 04 Plan 03: Training Engine & Calibration Summary

**Execution Status**: COMPLETE  
**Wave**: 2  
**Timestamp**: 2026-10-12  

---

### 1. Key Accomplishments
- **Dynamic Negative Resampling Training Loop**:
  - Implemented in `scripts/train_gnn_model.py`.
  - Replaced soft targets with `nn.BCEWithLogitsLoss()` on hard 0/1 binary labels.
  - Dynamically resamples 1:1 negative pairs uniformly from unlabeled candidate pairs of training drugs fresh every epoch.
  - Optimized with AdamW (`lr=1e-3`, `weight_decay=1e-5`) and `CosineAnnealingLR` schedule.
  - Applied leaf gradient accumulation across batched training drugs ($46,705$ nodes, $99,386$ edges), dropping training epoch time from 140s to ~11s.
- **5-Seed Inductive Benchmark**:
  - Ran 5 independent seeds (`42, 123, 456, 789, 1011`) evaluated on inductive cold-start S1 validation set (`val_pairs.csv`):
    - **Validation AUPRC**: $0.6155 \pm 0.0138$
    - **Validation AUROC**: $0.6267 \pm 0.0168$
    - **Best Seed**: Seed 42 (AUPRC: 0.6368, AUROC: 0.6541)
  - Saved individual seed checkpoints to `backend/trained_models/checkpoints/drugbank_gine_seed{seed}.pth`.
  - Saved best primary model checkpoint to `backend/trained_models/drugbank_gine_model.pth`.
  - Exported aggregated benchmark metrics to `reports/training_seeds_summary.json`.
- **Post-Hoc Temperature Scaling & Calibration**:
  - Implemented in `scripts/calibrate_model.py`.
  - Fits learnable scalar $T > 0$ to minimize NLL on held-out validation pairs with frozen representations.
  - Optimal Temperature: $T = 0.8231$ (Validation NLL: $0.6776$).
  - Generated visual reliability diagram at `reports/ddi_calibration_reliability_diagram.png`.
  - Exported calibration parameters to `backend/trained_models/model_calibration.json`.
  - Enforced terminology: output probabilities labeled `"interaction likelihood score"`.
- **Scientific Verification Tests**:
  - Created `backend/tests/test_ddi_calibration.py`.
  - All 3 tests passed cleanly (`test_temperature_scaling_optimization`, `test_ece_computation`, `test_calibrated_score_bounds`).

---

### 2. Artifacts Produced
- `scripts/train_gnn_model.py`
- `scripts/calibrate_model.py`
- `backend/trained_models/drugbank_gine_model.pth`
- `backend/trained_models/checkpoints/drugbank_gine_seed*.pth`
- `reports/training_seeds_summary.json`
- `backend/trained_models/model_calibration.json`
- `reports/ddi_calibration_reliability_diagram.png`
- `backend/tests/test_ddi_calibration.py`

---

### 3. Verification Summary
- Multi-seed execution: 5 seeds completed successfully.
- Pytest calibration tests: 3/3 passed (100%).
- Checkpoints and calibration JSON verified and present in `backend/trained_models/`.
