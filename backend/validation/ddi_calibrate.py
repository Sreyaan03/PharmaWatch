# backend/validation/ddi_calibrate.py
# ─────────────────────────────────────────────────────────────────────────────
# Calibration of the selected DDI model, and the deployment manifest.
#
# * The calibrator is fitted on the validation CALIBRATION half only (split by drug from the
#   half used to choose the model), so it never sees the data that selected the model.
# * Temperature scaling and isotonic regression are compared by cross-fitting inside the
#   calibration half (fit on one group of drugs, score the other). If neither lowers ECE the
#   model is shipped uncalibrated.
# * ECE is reported on the S0/S1/S2 test sets, which were used for neither fitting nor selection.
#   `calibrated` is true only when ECE on the S1 test set actually improved.
# * The score is called an "interaction likelihood score", not a probability of harm.
#
#   python -m validation.ddi_calibrate        # after python -m validation.ddi_eval
# ─────────────────────────────────────────────────────────────────────────────

import os
import sys
import json
import argparse
import numpy as np
import pandas as pd
import torch
import torch.nn as nn
import torch.optim as optim
from torch_geometric.data import Batch
import matplotlib.pyplot as plt

from core.config import SPLITS_DIR, MODEL_DIR, REPORTS_DIR
from ml.ddi.model import GINEDDIPredictor
from ml.ddi.data import precompute_drug_structures


class TemperatureScaler(nn.Module):
    """Learns post-hoc temperature scaling parameter T > 0 to calibrate logits."""
    def __init__(self):
        super().__init__()
        # Initialize temperature at 1.0 (log_temp = 0.0)
        self.log_temp = nn.Parameter(torch.zeros(1))

    @property
    def temperature(self):
        return torch.exp(self.log_temp).item()

    def forward(self, logits):
        return logits / torch.exp(self.log_temp)

    def fit(self, logits, labels, lr=0.01, max_iters=200):
        """Optimizes temperature to minimize BCE loss on validation logits."""
        criterion = nn.BCEWithLogitsLoss()
        optimizer = optim.Adam([self.log_temp], lr=lr)

        logits_t = torch.tensor(logits, dtype=torch.float)
        labels_t = torch.tensor(labels, dtype=torch.float)

        best_loss = float('inf')
        best_log_temp = 0.0

        for _ in range(max_iters):
            optimizer.zero_grad()
            scaled_logits = self(logits_t)
            loss = criterion(scaled_logits, labels_t)
            loss.backward()
            optimizer.step()

            if loss.item() < best_loss:
                best_loss = loss.item()
                best_log_temp = self.log_temp.item()

        self.log_temp.data = torch.tensor([best_log_temp])
        return self.temperature, best_loss


def compute_ece(probs, labels, n_bins=10):
    """
    Computes Expected Calibration Error (ECE) with equal-width probability bins.
    """
    bin_boundaries = np.linspace(0.0, 1.0, n_bins + 1)
    ece = 0.0
    total_samples = len(probs)
    bin_stats = []

    for i in range(n_bins):
        bin_lower = bin_boundaries[i]
        bin_upper = bin_boundaries[i + 1]
        mask = (probs >= bin_lower) & (probs < bin_upper) if i < n_bins - 1 else (probs >= bin_lower) & (probs <= bin_upper)
        bin_count = int(np.sum(mask))

        if bin_count > 0:
            bin_acc = float(np.mean(labels[mask]))
            bin_conf = float(np.mean(probs[mask]))
            bin_error = abs(bin_acc - bin_conf)
            ece += (bin_count / total_samples) * bin_error
            bin_stats.append({
                'bin_center': (bin_lower + bin_upper) / 2.0,
                'accuracy': bin_acc,
                'confidence': bin_conf,
                'count': bin_count,
                'error': bin_error
            })
        else:
            bin_stats.append({
                'bin_center': (bin_lower + bin_upper) / 2.0,
                'accuracy': 0.0,
                'confidence': (bin_lower + bin_upper) / 2.0,
                'count': 0,
                'error': 0.0
            })

    return float(ece), bin_stats


def plot_reliability_diagram(uncal_stats, cal_stats, uncal_ece, cal_ece, save_path):
    """Generates and saves visual reliability diagram before and after calibration."""
    os.makedirs(os.path.dirname(save_path), exist_ok=True)
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 6))

    # 1. Uncalibrated Reliability Diagram
    centers = [s['bin_center'] for s in uncal_stats]
    uncal_accs = [s['accuracy'] for s in uncal_stats]
    uncal_confs = [s['confidence'] for s in uncal_stats]

    ax1.plot([0, 1], [0, 1], 'k--', label='Perfect Calibration')
    ax1.bar(centers, uncal_accs, width=0.08, alpha=0.6, color='#2563eb', edgecolor='black', label='Empirical Accuracy')
    ax1.scatter(uncal_confs, uncal_accs, color='#dc2626', zorder=5, label='Bin Centers')
    ax1.set_title(f"Uncalibrated Model (ECE = {uncal_ece:.4f})", fontsize=12, fontweight='bold')
    ax1.set_xlabel("Predicted Interaction Likelihood Score", fontsize=10)
    ax1.set_ylabel("True Empirical Fraction of Interactions", fontsize=10)
    ax1.set_xlim(0, 1)
    ax1.set_ylim(0, 1)
    ax1.legend(loc='upper left')
    ax1.grid(True, linestyle=':', alpha=0.6)

    # 2. Calibrated Reliability Diagram
    cal_accs = [s['accuracy'] for s in cal_stats]
    cal_confs = [s['confidence'] for s in cal_stats]

    ax2.plot([0, 1], [0, 1], 'k--', label='Perfect Calibration')
    ax2.bar(centers, cal_accs, width=0.08, alpha=0.6, color='#10b981', edgecolor='black', label='Empirical Accuracy')
    ax2.scatter(cal_confs, cal_accs, color='#059669', zorder=5, label='Bin Centers')
    ax2.set_title(f"Calibrated via Temperature Scaling (ECE = {cal_ece:.4f})", fontsize=12, fontweight='bold')
    ax2.set_xlabel("Calibrated Interaction Likelihood Score", fontsize=10)
    ax2.set_ylabel("True Empirical Fraction of Interactions", fontsize=10)
    ax2.set_xlim(0, 1)
    ax2.set_ylim(0, 1)
    ax2.legend(loc='upper left')
    ax2.grid(True, linestyle=':', alpha=0.6)

    plt.suptitle("Drug-Drug Interaction Model Reliability Diagrams (DrugBank Inductive Val)", fontsize=14, fontweight='bold')
    plt.tight_layout()
    plt.savefig(save_path, dpi=300)
    plt.close()
    print(f"[PLOT] Saved reliability diagram to: {save_path}", flush=True)


# ── calibrators ──────────────────────────────────────────────────────────────
def _logit(p, eps=1e-6):
    p = np.clip(np.asarray(p, dtype=float), eps, 1 - eps)
    return np.log(p / (1 - p))


def _sigmoid(x):
    return 1.0 / (1.0 + np.exp(-np.asarray(x, dtype=float)))


def fit_temperature(probs, labels):
    """Fit T on the calibration data. Returns a function probs -> calibrated probs, and params."""
    scaler = TemperatureScaler()
    t, _ = scaler.fit(_logit(probs), labels)
    return (lambda p: _sigmoid(_logit(p) / t)), {"method": "temperature_scaling", "temperature": round(float(t), 6)}


def fit_isotonic(probs, labels):
    """Isotonic regression, stored as its piecewise-linear knots so it needs no pickle."""
    from sklearn.isotonic import IsotonicRegression
    iso = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip").fit(probs, labels)
    xs, ys = [float(v) for v in iso.X_thresholds_], [float(v) for v in iso.y_thresholds_]
    return (lambda p: np.interp(np.asarray(p, dtype=float), xs, ys)), \
        {"method": "isotonic", "x": [round(v, 9) for v in xs], "y": [round(v, 9) for v in ys]}


CALIBRATORS = {"temperature_scaling": fit_temperature, "isotonic": fit_isotonic}


def apply_calibration(params, probs):
    """Apply a stored calibrator (the dict written to the manifest) to raw scores."""
    method = params.get("method") if params else None
    if method == "temperature_scaling":
        return _sigmoid(_logit(probs) / params["temperature"])
    if method == "isotonic":
        return np.interp(np.asarray(probs, dtype=float), params["x"], params["y"])
    return np.asarray(probs, dtype=float)


def cross_fit_ece(fit, probs, labels, groups, n_bins=10):
    """ECE of a calibrator fitted on one group of drugs and scored on the other (both ways, pooled)."""
    groups = np.asarray(groups)
    unique = sorted(set(groups.tolist()))
    half = set(unique[::2])
    in_a = np.array([g in half for g in groups])
    out_p, out_y = [], []
    for train_mask in (in_a, ~in_a):
        if train_mask.sum() == 0 or (~train_mask).sum() == 0:
            continue
        f, _ = fit(probs[train_mask], labels[train_mask])
        out_p.append(f(probs[~train_mask]))
        out_y.append(labels[~train_mask])
    return compute_ece(np.concatenate(out_p), np.concatenate(out_y), n_bins)[0]


def choose_calibrator(probs, labels, groups, n_bins=10):
    """Pick by cross-fitted ECE on the calibration half. Returns (name or None, scores per option)."""
    scores = {"uncalibrated": compute_ece(probs, labels, n_bins)[0]}
    for name, fit in CALIBRATORS.items():
        scores[name] = cross_fit_ece(fit, probs, labels, groups, n_bins)
    best = min(scores, key=scores.get)
    return (None if best == "uncalibrated" else best), scores


def precision_threshold(probs, labels, target):
    """Smallest score whose top-set precision is at least `target`, on the data given. None if none."""
    order = np.argsort(-probs, kind="stable")
    y = labels[order]
    p = probs[order]
    cum_precision = np.cumsum(y) / (np.arange(len(y)) + 1)
    ok = np.where(cum_precision >= target)[0]
    if len(ok) == 0:
        return None
    return float(p[ok.max()])


# ── scoring any selected candidate on dataset drug ids ───────────────────────
def candidate_scores(candidate, pairs, ctx):
    """Raw interaction scores in [0, 1] for (drug_a, drug_b) pairs, for a selected candidate."""
    from validation import ddi_eval as E
    kind = candidate["kind"]
    if kind == "gine":
        model, _ = E.load_checkpoint_model(os.path.join(MODEL_DIR, candidate["checkpoint"]))
        index, emb = E.embed_all(model, ctx["all_drugs"], ctx["graphs"], ctx["fps"])
        return _sigmoid(E.gnn_logits(model, emb, index, pairs))
    if kind == "logreg":
        import pickle
        from ml.ddi.baselines import build_pair_feature_matrix
        with open(os.path.join(MODEL_DIR, "baseline_logreg.pkl"), "rb") as f:
            return pickle.load(f).predict_proba(build_pair_feature_matrix(pairs, ctx["drug_fps"]))[:, 1]
    if kind == "xgboost":
        import xgboost as xgb
        from ml.ddi.baselines import build_pair_feature_matrix
        m = xgb.XGBClassifier()
        m.load_model(os.path.join(MODEL_DIR, "baseline_xgb.json"))
        return m.predict_proba(build_pair_feature_matrix(pairs, ctx["drug_fps"]))[:, 1]
    raise ValueError(f"candidate kind {kind!r} cannot be deployed")


def scope_note(ece):
    """Where the calibrator helps and where it does not, from test ECE per regime."""
    worse = [r for r, v in ece.items() if v["after"] > v["before"]]
    better = [r for r, v in ece.items() if v["after"] < v["before"]]
    if not worse:
        return "ECE improved in every test regime."
    return (f"The calibrator is fitted on validation pairs with one unseen drug, and lowered ECE for "
            f"{', '.join(better) or 'no regime'}; it raised ECE for {', '.join(worse)}. Treat the score as "
            f"calibrated only for pairs involving a drug the model was not trained on.")


def decide_calibrated(chosen, ece_before, ece_after):
    """`calibrated` is true only when a calibrator was chosen and it lowered ECE on held-out data."""
    return bool(chosen) and ece_after < ece_before


def val_drug_of(pair, val_drugs):
    """The validation drug in a validation pair (the other drug is a training drug)."""
    return pair[0] if pair[0] in val_drugs else pair[1]


def main():
    parser = argparse.ArgumentParser(description="Calibrate the selected DDI model and write the deployment manifest.")
    parser.add_argument("--n_bins", type=int, default=10)
    parser.add_argument("--target_precision", type=float, default=0.80,
                        help="Precision the predicted-interaction threshold is chosen for, on the calibration half.")
    args = parser.parse_args()

    from ml.ddi.baselines import build_drug_fingerprints
    from ml.ddi.data import precompute_drug_structures
    from validation import ddi_eval as E

    results_path = os.path.join(REPORTS_DIR, "ddi_benchmark_results.json")
    if not os.path.exists(results_path):
        print("[ERROR] Run python -m validation.ddi_eval first.")
        sys.exit(1)
    with open(results_path) as f:
        bench = json.load(f)
    deployable = [c for c in bench["selection"] if c["kind"] in ("gine", "logreg", "xgboost")]
    candidate = max(deployable, key=lambda c: c["select_auprc"])
    overall_best = max(bench["selection"], key=lambda c: c["select_auprc"])
    print(f"[CALIB] Selected for deployment: {candidate['name']} "
          f"(selection AUPRC {candidate['select_auprc']:.4f})", flush=True)

    with open(os.path.join(SPLITS_DIR, "drug_metadata.json")) as f:
        meta = json.load(f)
    graphs, fps = precompute_drug_structures(meta)
    ctx = {"all_drugs": sorted(meta["drug_smiles"]), "graphs": graphs, "fps": fps,
           "drug_fps": build_drug_fingerprints(meta)}

    def load(name):
        df = pd.read_csv(os.path.join(SPLITS_DIR, name))
        pairs = [(r.drug_a, r.drug_b) for r in df.itertuples(index=False)]
        return pairs, df["label"].values.astype(float)

    calib_pairs, y_cal = load("val_calib_pairs.csv")
    p_cal = np.asarray(candidate_scores(candidate, calib_pairs, ctx), dtype=float)
    calib_val_drugs = set(meta["val_calib_drugs"])
    groups = [val_drug_of(p, calib_val_drugs) for p in calib_pairs]

    choice, cv_ece = choose_calibrator(p_cal, y_cal, groups, args.n_bins)
    print(f"[CALIB] Cross-fitted ECE on the calibration half: "
          f"{ {k: round(v, 4) for k, v in cv_ece.items()} } -> {choice or 'ship uncalibrated'}", flush=True)

    if choice:
        calibrate_fn, params = CALIBRATORS[choice](p_cal, y_cal)
    else:
        calibrate_fn, params = (lambda p: np.asarray(p, dtype=float)), {"method": None}

    # ECE on the test sets: used for neither fitting nor selection
    ece = {}
    test_scores = {}
    for regime, fn in E.REGIMES.items():
        pairs, y = load(fn)
        raw = np.asarray(candidate_scores(candidate, pairs, ctx), dtype=float)
        cal = calibrate_fn(raw)
        before, stats_b = compute_ece(raw, y, args.n_bins)
        after, stats_a = compute_ece(cal, y, args.n_bins)
        ece[regime] = {"before": round(before, 4), "after": round(after, 4)}
        test_scores[regime] = (raw, cal, y, stats_b, stats_a, before, after)
        print(f"[CALIB] {regime}: ECE {before:.4f} -> {after:.4f}", flush=True)

    primary = "S1 (One Unseen)"
    raw, cal, y, stats_b, stats_a, before, after = test_scores[primary]
    calibrated = decide_calibrated(choice, before, after)
    if choice and not calibrated:
        print("[CALIB] The calibrator did not lower ECE on the S1 test set: shipping uncalibrated.", flush=True)
        calibrate_fn, params, choice = (lambda p: np.asarray(p, dtype=float)), {"method": None}, None
        cal, after, stats_a = raw, before, stats_b

    plot_reliability_diagram(stats_b, stats_a, before, after,
                             os.path.join(REPORTS_DIR, "ddi_calibration_reliability_diagram.png"))

    # Threshold for "predicted interaction", chosen on the calibration half
    served_cal = calibrate_fn(p_cal)
    threshold = precision_threshold(served_cal, y_cal, args.target_precision)
    s1_precision = None
    if threshold is not None:
        flagged = cal >= threshold
        s1_precision = round(float(y[flagged].mean()), 4) if flagged.any() else None

    from faers.registry import data_version
    served_file = candidate.get("checkpoint") or {"logreg": "baseline_logreg.pkl", "xgboost": "baseline_xgb.json"}[candidate["kind"]]
    if candidate["kind"] == "gine":
        # per-seed checkpoints are git-ignored; the deployed one is copied to a tracked file
        import shutil
        shutil.copyfile(os.path.join(MODEL_DIR, served_file), os.path.join(MODEL_DIR, "ddi_deployed.pth"))
        served_file = "ddi_deployed.pth"
    manifest = {
        "model": {"name": candidate["name"], "kind": candidate["kind"], "file": served_file,
                  "source_checkpoint": candidate.get("checkpoint"), "seed": candidate.get("seed")},
        "selected_by": "highest AUPRC on the validation selection half (S1-style)",
        "selection_auprc": round(candidate["select_auprc"], 4),
        "best_overall_candidate": {"name": overall_best["name"], "select_auprc": round(overall_best["select_auprc"], 4),
                                   "deployable": overall_best["kind"] in ("gine", "logreg", "xgboost")},
        "model_version": f"ddi-{candidate['kind']}-" + (str(candidate.get("seed")) if candidate.get("seed") is not None else "baseline"),
        "training_data": "TDC DrugBank DDI, drug-level inductive split (data/splits)",
        "metrics": {"S1_test": next((r for r in bench["rows"]
                                     if r["Model"] == candidate["name"].split(",")[0] and r["Regime"] == primary), None)},
        "calibration": {**params, "fitted_on": "validation calibration half",
                        "candidates_cross_fitted_ece": {k: round(v, 4) for k, v in cv_ece.items()},
                        "test_ece": ece, "primary_regime": primary},
        "calibrated": calibrated,
        "calibration_scope": scope_note(ece),
        "score_terminology": "interaction likelihood score",
        "predicted_interaction_threshold": None if threshold is None else round(threshold, 4),
        "threshold_target_precision": args.target_precision,
        "threshold_chosen_on": "validation calibration half",
        "threshold_precision_on_S1_test": s1_precision,
        "faers_data_version_when_built": data_version(),
    }
    out = os.path.join(MODEL_DIR, "ddi_manifest.json")
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(manifest, f, indent=2)
    with open(os.path.join(MODEL_DIR, "model_calibration.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump({"calibrated": calibrated, **params, "test_ece": ece, "score_terminology": "interaction likelihood score"}, f, indent=2)
    print(f"[OK] Wrote {out}  (calibrated={calibrated})", flush=True)


if __name__ == "__main__":
    main()
