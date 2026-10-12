# backend/tests/test_interactions_api.py
# ─────────────────────────────────────────────────────────────────────────────
# Scientific & API Integration Verification for 3-Tier DDI Endpoints:
# 1. Tests 3-tier API response: documented, model_predicted, no_evidence_found.
# 2. Ensures zero occurrences of "safe" or "safe to combine" in predictions.
# 3. Verifies mandatory "Research use only, not clinical advice" disclaimer.
# 4. Verifies canonical order-insensitive pair cache keying.
# 5. Verifies polypharmacy endpoint 3-tier categorization and disclaimer.
# ─────────────────────────────────────────────────────────────────────────────

import os
import sys
import json
import pytest

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from app import app
from database import get_prediction, upsert_prediction


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


def test_3_tier_api_response(client):
    """Verifies that /api/graph/predict returns calibrated 3-tier response with disclaimers."""
    # Test with Aspirin and Acetaminophen
    payload = {
        "drug_a": "Aspirin",
        "drug_b": "Acetaminophen",
        "smiles_a": "CC(=O)Oc1ccccc1C(=O)O",
        "smiles_b": "CC(=O)Nc1ccc(O)cc1"
    }

    res = client.post("/api/graph/predict", json=payload)
    assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.data}"

    data = res.get_json()
    assert "tier" in data
    assert data["tier"] in {"documented", "model_predicted", "no_evidence_found"}
    assert "tier_description" in data
    assert "disclaimer" in data
    assert data["disclaimer"] == "Research use only, not clinical advice"

    # Calibration & probability fields
    assert "interaction_likelihood_score" in data
    score = data["interaction_likelihood_score"]
    assert 0.0 <= score <= 1.0, f"Expected probability score in [0, 1], got {score}"
    assert data.get("calibrated") is True

    # Critical Safety Terminology Check: Never return "safe" or "safe to combine"
    response_text = res.data.decode("utf-8").lower()
    assert "safe to combine" not in response_text, "Found forbidden phrase 'safe to combine' in response"
    assert "is safe" not in response_text, "Found forbidden phrase 'is safe' in response"


def test_canonical_cache_keying(client):
    """Verifies that order of drugs (A, B) vs (B, A) is canonically ordered and idempotent."""
    pair_ab = {
        "drug_a": "Aspirin",
        "drug_b": "Ibuprofen",
        "smiles_a": "CC(=O)Oc1ccccc1C(=O)O",
        "smiles_b": "CC(C)Cc1ccc(cc1)C(C)C(=O)O"
    }
    pair_ba = {
        "drug_a": "Ibuprofen",
        "drug_b": "Aspirin",
        "smiles_a": "CC(C)Cc1ccc(cc1)C(C)C(=O)O",
        "smiles_b": "CC(=O)Oc1ccccc1C(=O)O"
    }

    res_ab = client.post("/api/graph/predict", json=pair_ab)
    assert res_ab.status_code == 200
    data_ab = res_ab.get_json()

    res_ba = client.post("/api/graph/predict", json=pair_ba)
    assert res_ba.status_code == 200
    data_ba = res_ba.get_json()

    # Scores and tiers must be identical regardless of input order
    assert data_ab["tier"] == data_ba["tier"]
    assert abs(data_ab["interaction_likelihood_score"] - data_ba["interaction_likelihood_score"]) < 1e-4

    # Direct database retrieval must be order-invariant
    cached_ab = get_prediction("Aspirin", "Ibuprofen")
    cached_ba = get_prediction("Ibuprofen", "Aspirin")
    assert cached_ab is not None
    assert cached_ba is not None
    assert cached_ab["drug_a"] == cached_ba["drug_a"]
    assert cached_ab["drug_b"] == cached_ba["drug_b"]


def test_polypharmacy_endpoint_tiers_and_disclaimer(client):
    """Verifies that /api/interactions/polypharmacy returns 3 tiers, disclaimer, and no 'safe to combine'."""
    payload = {
        "drugs": ["Aspirin", "Ibuprofen"]
    }

    res = client.post("/api/interactions/polypharmacy", json=payload)
    assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.data}"

    data = res.get_json()
    assert "disclaimer" in data
    assert data["disclaimer"] == "Research use only, not clinical advice"
    assert "pairs" in data
    assert len(data["pairs"]) >= 1

    for pair in data["pairs"]:
        assert "tier" in pair
        assert pair["tier"] in {"documented", "model_predicted", "no_evidence_found"}
        assert "interaction_likelihood_score" in pair
        assert 0.0 <= pair["interaction_likelihood_score"] <= 1.0
        assert pair.get("calibrated") is True

    # Critical Safety Terminology Check
    response_text = res.data.decode("utf-8").lower()
    assert "safe to combine" not in response_text
