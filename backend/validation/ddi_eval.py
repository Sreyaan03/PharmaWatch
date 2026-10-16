# backend/validation/ddi_eval.py
# ─────────────────────────────────────────────────────────────────────────────
# DDI benchmark on disjoint inductive splits (S0 warm, S1 one drug unseen, S2 both unseen).
#
# * Every row of the report is a model that was trained: the GINE model and each ablation
#   variant are separately trained checkpoints (scripts/train_gnn_model.py), and the baselines
#   (logistic regression, XGBoost, nearest-neighbour Tanimoto) are fitted on the training split.
# * Metrics: AUROC, AUPRC and precision@k. The test sets have 1:1 negatives by construction, so
#   the base rate is 0.5 and precision@k is only meaningful against that.
# * GINE variants are reported as mean and standard deviation over their trained seeds.
# * The model to deploy is the one with the best AUPRC on the validation "selection" half; the
#   other half is for calibration (validation/ddi_calibrate.py).
#
#   python -m validation.ddi_eval
# ─────────────────────────────────────────────────────────────────────────────

import glob
import json
import os
import pickle

import numpy as np
import pandas as pd
import torch
from sklearn.metrics import average_precision_score, roc_auc_score

from core.config import SPLITS_DIR, MODEL_DIR, REPORTS_DIR
from ml.ddi.baselines import NearestNeighborTanimoto, build_drug_fingerprints, build_pair_feature_matrix
from ml.ddi.data import precompute_drug_structures
from ml.ddi.model import GINEDDIPredictor
from validation.metrics import precision_at_k

CHECKPOINTS_DIR = os.path.join(MODEL_DIR, "checkpoints")
VARIANT_LABELS = {
    "full": "GINE (full)",
    "no_fingerprint": "Ablation: no fingerprint branch",
    "no_edge_features": "Ablation: no edge features (GIN)",
    "concat_head": "Ablation: concatenation head",
}
REGIMES = {"S0 (Warm)": "test_s0_pairs.csv", "S1 (One Unseen)": "test_s1_pairs.csv",
           "S2 (Both Unseen)": "test_s2_pairs.csv"}
KS = (100, 1000)


def evaluate_predictions(y_true, scores):
    """AUROC, AUPRC and precision@k for one set of scores."""
    y = np.asarray(y_true, dtype=float)
    s = np.asarray(scores, dtype=float)
    out = {"AUROC": round(float(roc_auc_score(y, s)), 4), "AUPRC": round(float(average_precision_score(y, s)), 4)}
    for k in KS:
        out[f"P@{k}"] = round(float(precision_at_k(y, s, k)), 4)
    return out


def aggregate_seeds(per_seed):
    """{metric: (mean, std)} over a list of per-seed metric dicts (population standard deviation)."""
    keys = per_seed[0].keys()
    return {k: (round(float(np.mean([m[k] for m in per_seed])), 4),
                round(float(np.std([m[k] for m in per_seed])), 4)) for k in keys}


def pair_key(a, b):
    return (a, b) if a < b else (b, a)


# ── GINE scoring ─────────────────────────────────────────────────────────────
def load_checkpoint_model(path, device="cpu"):
    """(model, payload) from a training checkpoint; the architecture comes from its stored config."""
    payload = torch.load(path, map_location=device, weights_only=True)
    model = GINEDDIPredictor(node_in_dim=22, edge_in_dim=10, hidden_dim=128, num_layers=4,
                             dropout=0.1, **payload["config"]).to(device)
    model.load_state_dict(payload["model_state_dict"])
    model.eval()
    return model, payload


def embed_all(model, drug_ids, graph_dict, fp_dict, chunk=512):
    """{drug_id: embedding row index} and the embedding matrix, in eval mode."""
    from torch_geometric.data import Batch
    outs = []
    with torch.no_grad():
        for start in range(0, len(drug_ids), chunk):
            ids = drug_ids[start:start + chunk]
            graphs = Batch.from_data_list([graph_dict[d] for d in ids])
            fps = torch.stack([fp_dict[d] for d in ids]) if model.use_fingerprint else None
            outs.append(model.encoder(graphs, fps))
    return {d: i for i, d in enumerate(drug_ids)}, torch.cat(outs)


def gnn_logits(model, embeddings, index, pairs, chunk=16384):
    """Raw logits for (drug_a, drug_b) pairs, given precomputed embeddings."""
    ia = torch.tensor([index[a] for a, _ in pairs], dtype=torch.long)
    ib = torch.tensor([index[b] for _, b in pairs], dtype=torch.long)
    out = []
    with torch.no_grad():
        for s in range(0, len(pairs), chunk):
            out.append(model.pair_head(embeddings[ia[s:s + chunk]], embeddings[ib[s:s + chunk]]).squeeze(-1))
    return torch.cat(out).numpy()


def variant_checkpoints(variant):
    return sorted(glob.glob(os.path.join(CHECKPOINTS_DIR, f"ddi_{variant}_seed*.pth")))


# ── model selection ──────────────────────────────────────────────────────────
def select_best(candidates):
    """The candidate with the highest selection AUPRC. candidates: [{'name', 'select_auprc', ...}]."""
    return max(candidates, key=lambda c: c["select_auprc"])


# ── report ───────────────────────────────────────────────────────────────────
def _fmt(v):
    return f"{v[0]:.4f} ± {v[1]:.4f}" if isinstance(v, tuple) and v[1] else (f"{v[0]:.4f}" if isinstance(v, tuple) else f"{v:.4f}")


def ablation_findings(rows):
    """Statements the trained ablations support, from the S1 mean AUPRC of each variant vs full."""
    s1 = {r["Model"]: r for r in rows if r["Regime"] == "S1 (One Unseen)"}
    full = s1.get(VARIANT_LABELS["full"])
    findings = []
    if not full:
        return findings
    for variant in ("no_fingerprint", "no_edge_features", "concat_head"):
        r = s1.get(VARIANT_LABELS[variant])
        if not r:
            findings.append(f"{VARIANT_LABELS[variant]}: not trained, so no conclusion is drawn.")
            continue
        d = r["AUPRC"][0] - full["AUPRC"][0]
        spread = max(r["AUPRC"][1], full["AUPRC"][1])
        verdict = ("within the seed-to-seed spread, so no difference is shown"
                   if abs(d) <= spread else ("lower" if d < 0 else "higher") + " than the full model")
        findings.append(f"{VARIANT_LABELS[variant]}: S1 AUPRC {r['AUPRC'][0]:.4f} vs {full['AUPRC'][0]:.4f} "
                        f"(difference {d:+.4f}; {r['n_seeds']} vs {full['n_seeds']} seeds): {verdict}.")
    return findings


def write_report(rows, selection, findings, notes):
    os.makedirs(REPORTS_DIR, exist_ok=True)
    flat = []
    for r in rows:
        flat.append({"Model": r["Model"], "Regime": r["Regime"], "Seeds": r["n_seeds"],
                     **{m: _fmt(r[m]) for m in ("AUROC", "AUPRC", "P@100", "P@1000")}})
    df = pd.DataFrame(flat)
    df.to_csv(os.path.join(REPORTS_DIR, "ddi_benchmark_results.csv"), index=False)

    headers = list(df.columns)
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    lines += ["| " + " | ".join(str(x) for x in row) + " |" for row in df.itertuples(index=False)]
    sel_lines = ["| Candidate | Selection AUPRC |", "| --- | --- |"]
    sel_lines += [f"| {c['name']} | {c['select_auprc']:.4f} |" for c in sorted(selection, key=lambda c: -c["select_auprc"])]

    md = ["# Drug-drug interaction model: benchmark and ablations", "",
          "TDC DrugBank DDI, disjoint inductive splits. Every row is a trained model. "
          "GINE rows are mean ± standard deviation over the trained seeds.", "",
          *lines, "", "## Model selection (validation selection half, S1-style)", "", *sel_lines, "",
          "## What the trained ablations support", "", *[f"- {f}" for f in findings], "",
          "## Notes", "", *[f"- {n}" for n in notes], ""]
    with open(os.path.join(REPORTS_DIR, "ddi_benchmark_results.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(md))
    with open(os.path.join(REPORTS_DIR, "ddi_benchmark_results.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump({"rows": rows, "selection": selection, "findings": findings}, f, indent=2, default=list)


# ── the benchmark ────────────────────────────────────────────────────────────
def run_benchmark():
    with open(os.path.join(SPLITS_DIR, "drug_metadata.json")) as f:
        meta = json.load(f)
    graphs, fps = precompute_drug_structures(meta)
    all_drugs = sorted(meta["drug_smiles"])
    drug_fps = build_drug_fingerprints(meta)

    regimes = {name: pd.read_csv(os.path.join(SPLITS_DIR, fn)) for name, fn in REGIMES.items()}
    select_df = pd.read_csv(os.path.join(SPLITS_DIR, "val_select_pairs.csv"))
    train_pos_df = pd.read_csv(os.path.join(SPLITS_DIR, "train_pairs.csv"))
    pairs_of = lambda df: [(r.drug_a, r.drug_b) for r in df.itertuples(index=False)]   # noqa: E731

    rows, selection, best_by_variant = [], [], {}

    # GINE variants: one trained model per seed
    for variant in VARIANT_LABELS:
        per_regime = {name: [] for name in regimes}
        sel_scores = []
        for path in variant_checkpoints(variant):
            model, payload = load_checkpoint_model(path)
            index, emb = embed_all(model, all_drugs, graphs, fps)
            for name, df in regimes.items():
                per_regime[name].append(evaluate_predictions(
                    df["label"].values, gnn_logits(model, emb, index, pairs_of(df))))
            sel = evaluate_predictions(select_df["label"].values,
                                       gnn_logits(model, emb, index, pairs_of(select_df)))
            sel_scores.append((sel["AUPRC"], path, payload))
        if not sel_scores:
            continue
        for name, per_seed in per_regime.items():
            rows.append({"Model": VARIANT_LABELS[variant], "Regime": name, "n_seeds": len(per_seed),
                         **aggregate_seeds(per_seed)})
        best = max(sel_scores, key=lambda t: t[0])
        best_by_variant[variant] = best
        if variant == "full":
            selection.append({"name": f"GINE (full), best of {len(sel_scores)} seeds", "kind": "gine",
                              "select_auprc": best[0], "checkpoint": os.path.relpath(best[1], MODEL_DIR).replace(os.sep, "/"),
                              "seed": best[2]["seed"]})

    # Baselines, fitted on the training split
    with open(os.path.join(MODEL_DIR, "baseline_logreg.pkl"), "rb") as f:
        logreg = pickle.load(f)
    import xgboost as xgb
    xgb_model = xgb.XGBClassifier()
    xgb_model.load_model(os.path.join(MODEL_DIR, "baseline_xgb.json"))
    nn = NearestNeighborTanimoto(train_pos_df, drug_fps)

    scorers = {
        "Logistic regression": ("logreg", lambda pr: logreg.predict_proba(build_pair_feature_matrix(pr, drug_fps))[:, 1]),
        "XGBoost": ("xgboost", lambda pr: xgb_model.predict_proba(build_pair_feature_matrix(pr, drug_fps))[:, 1]),
        "NN-Tanimoto": ("nn_tanimoto", lambda pr: nn.predict_batch(pr)),
    }
    for label, (kind, score) in scorers.items():
        for name, df in regimes.items():
            rows.append({"Model": label, "Regime": name, "n_seeds": 1,
                         **{k: (v, 0.0) for k, v in evaluate_predictions(df["label"].values, score(pairs_of(df))).items()}})
        sel = evaluate_predictions(select_df["label"].values, score(pairs_of(select_df)))
        selection.append({"name": label, "kind": kind, "select_auprc": sel["AUPRC"]})

    findings = ablation_findings(rows)
    best = select_best(selection)
    notes = [
        "Test sets have 1:1 sampled negatives, so the base rate is 0.5; precision@k is read against that. "
        "Negatives are unlabelled pairs, not confirmed non-interactions.",
        f"Deployed by validation selection: {best['name']} (selection AUPRC {best['select_auprc']:.4f}).",
        "Selection and calibration use different halves of the validation pairs (split by drug).",
    ]
    write_report(rows, selection, findings, notes)
    print(open(os.path.join(REPORTS_DIR, "ddi_benchmark_results.md"), encoding="utf-8").read())
    return rows, selection, best_by_variant


if __name__ == "__main__":
    run_benchmark()
