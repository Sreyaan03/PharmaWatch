# backend/ml/ddi/inference.py
# Scores a SMILES pair with whichever model the deployment manifest names.
#
# trained_models/ddi_manifest.json is written by validation/ddi_calibrate.py after the
# benchmark has chosen the model (highest AUPRC on the validation selection half). Nothing in
# this file chooses a model or decides whether it is calibrated; it reads both from the manifest.

import json
import os

import numpy as np

from core.config import MODEL_DIR
import logging
log = logging.getLogger(__name__)

MANIFEST_PATH = os.path.join(MODEL_DIR, "ddi_manifest.json")
DEFAULT_THRESHOLD = 0.5          # used only when the manifest has no precision-based threshold

_manifest = None
_scorer = None
_loaded = False


def load_manifest(path=None):
    """The deployment manifest, or None when no model has been deployed."""
    global _manifest
    path = path or MANIFEST_PATH
    if _manifest is None and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            _manifest = json.load(f)
    return _manifest


def reset():
    """Forget the cached manifest and model (used by tests and after redeploying)."""
    global _manifest, _scorer, _loaded
    _manifest, _scorer, _loaded = None, None, False


def _gine_scorer(manifest):
    import torch
    from ml.ddi.model import GINEDDIPredictor, smiles_to_fingerprint, smiles_to_molecular_graph
    payload = torch.load(os.path.join(MODEL_DIR, manifest["model"]["file"]), map_location="cpu",
                         weights_only=True)
    model = GINEDDIPredictor(node_in_dim=22, edge_in_dim=10, hidden_dim=128, num_layers=4,
                             dropout=0.1, **payload["config"])
    model.load_state_dict(payload["model_state_dict"])
    model.eval()

    def score(smiles_a, smiles_b):
        g1, g2 = smiles_to_molecular_graph(smiles_a), smiles_to_molecular_graph(smiles_b)
        if g1 is None or g2 is None:
            raise ValueError("Invalid SMILES - could not build molecular graph")
        with torch.no_grad():
            logit = model(g1, g2, smiles_to_fingerprint(smiles_a), smiles_to_fingerprint(smiles_b)).item()
        return float(1.0 / (1.0 + np.exp(-logit)))
    return score


def _sklearn_scorer(manifest):
    from ml.ddi.baselines import build_pair_feature_matrix
    from ml.ddi.model import smiles_to_fingerprint
    kind, path = manifest["model"]["kind"], os.path.join(MODEL_DIR, manifest["model"]["file"])
    if kind == "xgboost":
        import xgboost as xgb
        model = xgb.XGBClassifier()
        model.load_model(path)
    else:
        import pickle
        with open(path, "rb") as f:
            model = pickle.load(f)

    def score(smiles_a, smiles_b):
        fps = {"a": smiles_to_fingerprint(smiles_a).numpy(), "b": smiles_to_fingerprint(smiles_b).numpy()}
        return float(model.predict_proba(build_pair_feature_matrix([("a", "b")], fps))[:, 1][0])
    return score


def _load_scorer():
    global _scorer, _loaded
    if _loaded:
        return _scorer
    _loaded = True
    manifest = load_manifest()
    if manifest is None:
        log.warning("[DDI] no deployment manifest; interaction scoring is unavailable")
        return None
    try:
        kind = manifest["model"]["kind"]
        _scorer = _gine_scorer(manifest) if kind == "gine" else _sklearn_scorer(manifest)
        log.info(f"[DDI] loaded {manifest['model']['name']} ({manifest['model_version']})")
    except Exception as exc:
        log.error(f"[DDI] could not load the manifest's model: {exc}")
    return _scorer


def score_pair(smiles_a: str, smiles_b: str) -> dict:
    """Score a pair with the deployed model.

    Returns {'score', 'raw_score', 'calibrated', 'predicted_interaction', 'threshold',
             'model_version', 'model_name'}. `score` is the calibrated value only when the
    manifest says the calibrator improved ECE; otherwise it equals `raw_score`.
    """
    scorer = _load_scorer()
    manifest = load_manifest()
    if scorer is None or manifest is None:
        raise RuntimeError("DDI model not available")

    from validation.ddi_calibrate import apply_calibration
    raw = scorer(smiles_a, smiles_b)
    calibrated = bool(manifest.get("calibrated"))
    score = float(apply_calibration(manifest["calibration"], np.array([raw]))[0]) if calibrated else raw
    threshold = manifest.get("predicted_interaction_threshold")
    threshold = DEFAULT_THRESHOLD if threshold is None else threshold
    return {
        "score": round(score, 4),
        "raw_score": round(raw, 4),
        "calibrated": calibrated,
        "predicted_interaction": bool(score >= threshold),
        "threshold": threshold,
        "model_version": manifest["model_version"],
        "model_name": manifest["model"]["name"],
    }


def _run_gnn_pair(smiles_a: str, smiles_b: str):
    """Compatibility wrapper: (predicted_interaction, score). Prefer score_pair."""
    r = score_pair(smiles_a, smiles_b)
    return r["predicted_interaction"], r["score"]
