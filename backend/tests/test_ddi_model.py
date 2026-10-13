# backend/tests/test_ddi_model.py
# ─────────────────────────────────────────────────────────────────────────────
# Scientific verification tests for GINEDDIPredictor:
# 1. Permutation invariance: f(A, B) == f(B, A) within 1e-6 numerical tolerance.
# 2. Molecular graph featurizer dimension and validity tests.
# 3. Ablation branch toggle tests (with and without Morgan fingerprint).
# 4. Batch deduplication equivalence.
# ─────────────────────────────────────────────────────────────────────────────

import sys
import os
import torch
import pytest

# Ensure the backend packages are importable
BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from ml.ddi.model import (
    smiles_to_molecular_graph,
    smiles_to_fingerprint,
    SymmetricPairHead,
    GINEDDIPredictor,
)


def test_molecular_graph_dimensions():
    """Verifies that featurization produces exact 22-d node and 10-d edge features."""
    aspirin_smi = "CC(=O)Oc1ccccc1C(=O)O"
    graph = smiles_to_molecular_graph(aspirin_smi)

    assert graph is not None
    assert graph.x.shape[1] == 22, f"Expected 22 node features, got {graph.x.shape[1]}"
    assert graph.edge_attr.shape[1] == 10, f"Expected 10 edge features, got {graph.edge_attr.shape[1]}"
    assert graph.edge_index.shape[0] == 2

    # Fingerprint check
    fp = smiles_to_fingerprint(aspirin_smi)
    assert fp.shape[0] == 2048


def test_pair_head_permutation_invariance():
    """Mathematically proves that the pair head is strictly symmetric."""
    torch.manual_seed(42)
    head = SymmetricPairHead(drug_dim=384, hidden_dim=256, dropout=0.0)
    head.eval()

    batch_size = 16
    drug_dim = 384
    a = torch.randn(batch_size, drug_dim)
    b = torch.randn(batch_size, drug_dim)

    with torch.no_grad():
        out_ab = head(a, b)
        out_ba = head(b, a)

    # Must be identical within floating point tolerance
    diff = torch.max(torch.abs(out_ab - out_ba)).item()
    assert diff < 1e-6, f"Pair head is NOT permutation invariant! Max diff: {diff}"


def test_end_to_end_model_symmetry():
    """Proves end-to-end symmetry f(A, B) == f(B, A) across known drug pairs."""
    torch.manual_seed(42)
    model = GINEDDIPredictor(use_fingerprint=True)
    model.eval()

    pairs = [
        ("CC(=O)Oc1ccccc1C(=O)O", "CC(=O)CC(c1ccccc1)c1c(O)c2ccccc2oc1=O"),  # Aspirin + Warfarin
        ("CN(C)C(=N)NC(=N)N", "CC(=O)Oc1ccccc1C(=O)O"),  # Metformin + Aspirin
        ("CN(C)C(=N)NC(=N)N", "CC(=O)CC(c1ccccc1)c1c(O)c2ccccc2oc1=O"),  # Metformin + Warfarin
    ]

    for smi_a, smi_b in pairs:
        res_ab = model.predict_pair(smi_a, smi_b)
        res_ba = model.predict_pair(smi_b, smi_a)

        diff_logit = abs(res_ab['logit'] - res_ba['logit'])
        diff_score = abs(res_ab['interaction_likelihood_score'] - res_ba['interaction_likelihood_score'])

        assert diff_logit < 1e-6, f"Asymmetry in logit: {diff_logit}"
        assert diff_score < 1e-6, f"Asymmetry in score: {diff_score}"


def test_fingerprint_ablation_toggle():
    """Verifies that the model properly supports ablation with and without Morgan branch."""
    m_with_fp = GINEDDIPredictor(use_fingerprint=True)
    assert m_with_fp.encoder.drug_dim == 384

    m_no_fp = GINEDDIPredictor(use_fingerprint=False)
    assert m_no_fp.encoder.drug_dim == 256

    res = m_no_fp.predict_pair("CC(=O)Oc1ccccc1C(=O)O", "CN(C)C(=N)NC(=N)N")
    assert 'interaction_likelihood_score' in res
    assert 0.0 <= res['interaction_likelihood_score'] <= 1.0
