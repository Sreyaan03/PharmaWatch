"""
DDI evaluation honesty and manifest-driven deployment (Phase 8, plan 02).

  * every ablation is a separately trained model with its own architecture flag;
  * results are aggregated over seeds and the findings only claim differences beyond the spread;
  * the served model, its calibration flag and its version come from the manifest;
  * the loaded checkpoint format is the safe (weights-only) one.
"""

import json
import os
import sys

import numpy as np
import pytest
import torch

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from ml.ddi import inference  # noqa: E402
from ml.ddi.model import GINEDDIPredictor, smiles_to_fingerprint, smiles_to_molecular_graph  # noqa: E402
from validation import ddi_eval as E  # noqa: E402

ASPIRIN = "CC(=O)Oc1ccccc1C(=O)O"
PARACETAMOL = "CC(=O)Nc1ccc(O)cc1"


# ── trained variants differ in architecture, not in a switch applied afterwards ─
def test_no_edge_features_model_ignores_bond_features():
    torch.manual_seed(0)
    m = GINEDDIPredictor(use_edge_features=False).eval()
    g = smiles_to_molecular_graph(ASPIRIN)
    fp = smiles_to_fingerprint(ASPIRIN).unsqueeze(0)
    base = m.encoder(g, fp)
    g2 = g.clone()
    g2.edge_attr = torch.randn_like(g2.edge_attr)
    assert torch.allclose(base, m.encoder(g2, fp), atol=1e-6)
    full = GINEDDIPredictor(use_edge_features=True).eval()
    assert not torch.allclose(full.encoder(g, fp), full.encoder(g2, fp), atol=1e-6)


def test_concat_head_is_directional_and_symmetric_head_is_not():
    torch.manual_seed(0)
    a, b = torch.randn(8, 384), torch.randn(8, 384)
    sym = GINEDDIPredictor(head_type="symmetric").eval().pair_head
    cat = GINEDDIPredictor(head_type="concat").eval().pair_head
    assert torch.allclose(sym(a, b), sym(b, a), atol=1e-6)
    assert not torch.allclose(cat(a, b), cat(b, a), atol=1e-4)
    with pytest.raises(ValueError):
        GINEDDIPredictor(head_type="dot_product")


def test_variants_have_different_parameters():
    full = sum(p.numel() for p in GINEDDIPredictor().parameters())
    no_fp = sum(p.numel() for p in GINEDDIPredictor(use_fingerprint=False).parameters())
    concat = sum(p.numel() for p in GINEDDIPredictor(head_type="concat").parameters())
    assert len({full, no_fp, concat}) == 3


def test_the_benchmark_has_no_untrained_stand_in_rows():
    src = open(os.path.join(BASE_DIR, "validation", "ddi_eval.py"), encoding="utf-8").read()
    for forbidden in ("zero_edge_attr", "asym_logits", "Recall@", "gine_nofp_model"):
        assert forbidden not in src, forbidden
    assert "variant_checkpoints" in src and "load_checkpoint_model" in src


# ── aggregation and findings ─────────────────────────────────────────────────
def test_aggregate_seeds_gives_mean_and_spread():
    out = E.aggregate_seeds([{"AUPRC": 0.80}, {"AUPRC": 0.82}, {"AUPRC": 0.84}])
    assert out["AUPRC"] == (0.82, pytest.approx(0.0163, abs=1e-4))


def test_precision_at_k_and_metrics_are_reported():
    y = np.array([1, 1, 0, 0, 1, 0])
    s = np.array([0.9, 0.8, 0.7, 0.6, 0.5, 0.4])
    m = E.evaluate_predictions(y, s)
    assert m["P@100"] == 0.5 and "Recall@100" not in m and 0.5 < m["AUROC"] <= 1.0


def _row(model, auprc, spread, seeds):
    return {"Model": model, "Regime": "S1 (One Unseen)", "AUPRC": (auprc, spread), "n_seeds": seeds}


def test_findings_claim_only_differences_beyond_the_seed_spread():
    rows = [_row("GINE (full)", 0.82, 0.01, 5), _row("Ablation: no fingerprint branch", 0.815, 0.012, 3),
            _row("Ablation: no edge features (GIN)", 0.70, 0.01, 3)]
    text = " ".join(E.ablation_findings(rows))
    assert "no difference is shown" in text
    assert "lower than the full model" in text
    assert "concatenation head: not trained" in text


def test_selection_picks_the_highest_selection_auprc():
    assert E.select_best([{"name": "a", "select_auprc": 0.7}, {"name": "b", "select_auprc": 0.8}])["name"] == "b"


# ── manifest-driven inference ────────────────────────────────────────────────
@pytest.fixture
def deployed(tmp_path, monkeypatch):
    """A tiny GINE checkpoint plus a manifest, in a temporary model directory."""
    torch.manual_seed(1)
    cfg = {"use_fingerprint": True, "use_edge_features": True, "head_type": "symmetric"}
    model = GINEDDIPredictor(**cfg)
    ckpt = tmp_path / "checkpoints"
    ckpt.mkdir()
    torch.save({"variant": "full", "seed": 7, "config": cfg, "model_state_dict": model.state_dict(),
                "best_epoch": 1, "val_select_auprc": 0.8}, ckpt / "ddi_full_seed7.pth")

    def write(**over):
        manifest = {
            "model": {"name": "GINE (full)", "kind": "gine", "file": "checkpoints/ddi_full_seed7.pth", "seed": 7},
            "model_version": "ddi-gine-7", "calibrated": False,
            "calibration": {"method": "temperature_scaling", "temperature": 4.0},
            "predicted_interaction_threshold": None, **over}
        (tmp_path / "ddi_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        inference.reset()
    from core import config
    monkeypatch.setattr(config, "MODEL_DIR", str(tmp_path))         # the local storage area "models"
    monkeypatch.setattr(inference, "MANIFEST_PATH", str(tmp_path / "ddi_manifest.json"))
    write()
    yield write
    inference.reset()


def test_the_served_model_and_version_come_from_the_manifest(deployed):
    r = inference.score_pair(ASPIRIN, PARACETAMOL)
    assert r["model_version"] == "ddi-gine-7" and r["model_name"] == "GINE (full)"
    assert 0.0 <= r["score"] <= 1.0


def test_uncalibrated_manifest_serves_the_raw_score_and_says_so(deployed):
    r = inference.score_pair(ASPIRIN, PARACETAMOL)
    assert r["calibrated"] is False and r["score"] == r["raw_score"]


def test_calibrated_manifest_applies_the_stored_calibrator(deployed):
    deployed(calibrated=True)
    r = inference.score_pair(ASPIRIN, PARACETAMOL)
    assert r["calibrated"] is True
    logit = np.log(r["raw_score"] / (1 - r["raw_score"]))
    assert r["score"] == pytest.approx(1 / (1 + np.exp(-logit / 4.0)), abs=2e-4)


def test_predicted_interaction_uses_the_manifest_threshold(deployed):
    raw = inference.score_pair(ASPIRIN, PARACETAMOL)["raw_score"]
    deployed(predicted_interaction_threshold=raw - 0.01)
    assert inference.score_pair(ASPIRIN, PARACETAMOL)["predicted_interaction"] is True
    deployed(predicted_interaction_threshold=min(raw + 0.01, 0.999))
    assert inference.score_pair(ASPIRIN, PARACETAMOL)["predicted_interaction"] is False


def test_scoring_is_symmetric(deployed):
    a, b = inference.score_pair(ASPIRIN, PARACETAMOL), inference.score_pair(PARACETAMOL, ASPIRIN)
    assert a["raw_score"] == pytest.approx(b["raw_score"], abs=1e-4)


def test_without_a_manifest_scoring_is_unavailable_not_invented(tmp_path, monkeypatch):
    monkeypatch.setattr(inference, "MANIFEST_PATH", str(tmp_path / "none.json"))
    inference.reset()
    with pytest.raises(RuntimeError):
        inference.score_pair(ASPIRIN, PARACETAMOL)
    inference.reset()


def test_checkpoints_load_with_weights_only(tmp_path):
    cfg = {"use_fingerprint": False, "use_edge_features": True, "head_type": "symmetric"}
    path = tmp_path / "c.pth"
    torch.save({"config": cfg, "model_state_dict": GINEDDIPredictor(**cfg).state_dict(), "seed": 1}, path)
    payload = torch.load(path, map_location="cpu", weights_only=True)        # would raise on arbitrary objects
    assert payload["config"] == cfg
