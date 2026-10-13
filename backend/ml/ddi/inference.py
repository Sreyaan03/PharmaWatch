# backend/ml/ddi/inference.py
# Loads the deployed DDI model and scores a SMILES pair.

import os

from core.config import MODEL_DIR
import logging
log = logging.getLogger(__name__)

MODEL_PATH = os.path.join(MODEL_DIR, "drugbank_gine_model.pth")
CALIBRATION_PATH = os.path.join(MODEL_DIR, "model_calibration.json")

# ── GNN Model Lazy Loader & Temperature Scaling ───────────────────────────────
_gnn_model       = None
_gnn_loaded      = False
_gnn_temperature = 1.0

def get_gnn_temperature() -> float:
    global _gnn_temperature
    if os.path.exists(CALIBRATION_PATH):
        try:
            import json as _json
            with open(CALIBRATION_PATH, 'r') as f:
                data = _json.load(f)
                _gnn_temperature = float(data.get("optimal_temperature", 1.0))
        except Exception:
            _gnn_temperature = 1.0
    return _gnn_temperature

def _load_gnn():
    global _gnn_model, _gnn_loaded
    if _gnn_loaded:
        return _gnn_model
    try:
        from ml.ddi.model import GINEDDIPredictor
        import torch as _torch
        # Architecture: GINEConv x4, hidden_dim=128, multi-pooling, Morgan FP branch
        m = GINEDDIPredictor(
            node_in_dim=22,
            edge_in_dim=10,
            hidden_dim=128,
            num_layers=4,
            dropout=0.1,
            use_fingerprint=True
        )
        if os.path.exists(MODEL_PATH):
            m.load_state_dict(_torch.load(MODEL_PATH, map_location="cpu"))
            m.eval()
            _gnn_model = m
            log.info("[GNN] GINEDDIPredictor model loaded OK from drugbank_gine_model.pth")
        else:
            log.warning(f"[GNN] Model checkpoint not found at {MODEL_PATH}")
        _gnn_loaded = True
    except Exception as e:
        log.error(f"[GNN] Could not load model: {e}")
        _gnn_loaded = True
    return _gnn_model


# ── GNN Pair Execution ───────────────────────────────────────────────────────
def _run_gnn_pair(smiles_a: str, smiles_b: str):
    """
    Run the trained GINE DDI model with temperature scaling on a SMILES pair.
    Returns (is_harmful: bool, interaction_likelihood_score: float).
    """
    from ml.ddi.model import smiles_to_molecular_graph, smiles_to_fingerprint
    import torch as _torch

    gnn = _load_gnn()
    if gnn is None:
        raise RuntimeError("GNN model not available")

    g1 = smiles_to_molecular_graph(smiles_a)
    g2 = smiles_to_molecular_graph(smiles_b)
    if g1 is None or g2 is None:
        raise ValueError("Invalid SMILES - could not build molecular graph")

    fp1 = smiles_to_fingerprint(smiles_a)
    fp2 = smiles_to_fingerprint(smiles_b)

    temperature = get_gnn_temperature()

    with _torch.no_grad():
        out = gnn(g1, g2, fp1, fp2)
        scaled_logit = out / temperature
        prob = _torch.sigmoid(scaled_logit).item()

    is_harmful = prob >= 0.50
    return is_harmful, round(prob, 4)
