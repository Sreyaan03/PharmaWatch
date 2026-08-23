# backend/routes/ml.py
# ─────────────────────────────────────────────────────────────────────────────
# Machine Learning routes: LSTM model training, forecasting and gap analysis
# ─────────────────────────────────────────────────────────────────────────────

import sys
import os
import numpy as np
from flask import Blueprint, request, jsonify

# Ensure backend directory is in sys.path
backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

try:
    from lstm_model import train_model as lstm_train, predict as lstm_predict, scan_warning_gap
    LSTM_AVAILABLE = True
except ImportError:
    LSTM_AVAILABLE = False

from helpers.external_apis import fetch_trial_monitoring
from helpers.http_client import http_requests

ml_bp = Blueprint("ml", __name__)

@ml_bp.route("/api/lstm/train", methods=["POST"])
def train_lstm():
    """Train a real PyTorch LSTM on openFDA historical data for a given drug."""
    if not LSTM_AVAILABLE:
        return jsonify({"error": "lstm_model.py not loaded"}), 500
    drug = request.args.get('drug', 'Metformin')
    epochs = int(request.args.get('epochs', 100))
    try:
        _, error = lstm_train(drug, epochs=epochs)
        if error:
            return jsonify({"error": error}), 400
        return jsonify({"status": "ok", "drug": drug,
                        "message": f"LSTM trained and saved for '{drug}'"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@ml_bp.route("/api/lstm", methods=["GET"])
def get_lstm_forecast():
    """
    Returns real LSTM predictions vs actual FAERS counts.
    If model not yet trained, falls back to the rolling average.
    """
    drug = request.args.get('drug', 'Metformin')

    if LSTM_AVAILABLE:
        try:
            result, error = lstm_predict(drug)
            if result:
                return jsonify(result)
            print(f"[LSTM] Predict error for '{drug}': {error} — using fallback")
        except Exception as e:
            print(f"[LSTM] Exception: {e} — using fallback")

    FDA_BASE = "https://api.fda.gov/drug/event.json"
    try:
        url = f'{FDA_BASE}?search=patient.drug.medicinalproduct:"{drug}"&count=receivedate'
        res = http_requests.get(url).json()
        results = res.get("results", [])
        raw_counts = [r["count"] for r in results]
        if len(raw_counts) < 30:
            raw_counts = (raw_counts * 5)[:30]
        else:
            raw_counts = raw_counts[-30:]
        actual = raw_counts
        baseline = []
        window = 5
        for i in range(len(actual)):
            start = max(0, i - window)
            avg = np.mean(actual[start:i+1])
            baseline.append(round(float(avg), 1))
        labels = [f"Day {i+1}" for i in range(len(actual))]
        return jsonify({"drug": drug, "labels": labels,
                        "baseline": baseline, "actual": actual,
                        "fallback": True, "note": "Using rolling average — train LSTM via POST /api/lstm/train"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@ml_bp.route("/api/lstm/gap-scan", methods=["GET"])
def lstm_gap_scan():
    """Warning Gap Predictor (enhanced with ClinicalTrials.gov)."""
    if not LSTM_AVAILABLE:
        return jsonify({"error": "lstm_model.py not loaded"}), 500
    drug = request.args.get('drug', 'Metformin')
    try:
        result = scan_warning_gap(drug)
        if "error" in result:
            return jsonify(result)

        ct_data = fetch_trial_monitoring(drug)
        being_monitored = ct_data["being_monitored"]
        active_studies = ct_data["active_studies"]
        phase34_studies = ct_data["phase34_studies"]

        original_risk = result["risk_level"]
        rising = result["rising_signal"]
        has_warning = result["has_boxed_warning"]

        if rising and not has_warning and not being_monitored:
            upgraded_risk = "HIGH"
            ct_note = (
                f"⚠ CRITICAL GAP: Rising FAERS signal AND no active ClinicalTrials monitoring "
                f"for '{drug}'. Neither FDA labels nor research community has flagged this risk."
            )
        elif rising and not has_warning and being_monitored:
            upgraded_risk = "MODERATE"
            ct_note = (
                f"📋 {active_studies} active ClinicalTrials stud{'y' if active_studies==1 else 'ies'} "
                f"for '{drug}' ({phase34_studies} Phase 3/4). Rising signal is under trial observation, "
                f"but no FDA Boxed Warning yet issued."
            )
        elif not rising and not has_warning and not being_monitored:
            upgraded_risk = "MINIMAL"
            ct_note = f"No active ClinicalTrials found for '{drug}'. Stable signal with no regulatory concern."
        else:
            upgraded_risk = original_risk
            if being_monitored:
                ct_note = (
                    f"✅ {active_studies} active ClinicalTrials stud{'y' if active_studies==1 else 'ies'} "
                    f"monitoring '{drug}' ({phase34_studies} Phase 3/4). Signal is being tracked."
                )
            else:
                ct_note = f"No active ClinicalTrials studies found for '{drug}'."

        result["risk_level"] = upgraded_risk
        result["clinicaltrials"] = {
            "active_studies": active_studies,
            "phase34_studies": phase34_studies,
            "being_monitored": being_monitored,
            "note": ct_note
        }

        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
