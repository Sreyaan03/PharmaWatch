# backend/services/forecasting.py
# Business logic for backend/routes/ml.py (no Flask imports).

import numpy as np
from clients.clinicaltrials import fetch_trial_monitoring
from clients.http import http_requests
import logging
log = logging.getLogger(__name__)

try:
    from ml.forecast import train_model as lstm_train, predict as lstm_predict, scan_warning_gap
    LSTM_AVAILABLE = True
except ImportError:
    LSTM_AVAILABLE = False

def get_lstm_forecast(drug):
    """
    Returns real LSTM predictions vs actual FAERS counts.
    If model not yet trained, falls back to the rolling average.
    """
    if LSTM_AVAILABLE:
        try:
            result, error = lstm_predict(drug)
            if result:
                return result, 200
            log.warning(f"[LSTM] Predict error for '{drug}': {error} — using fallback")
        except Exception as e:
            log.warning(f"[LSTM] Exception: {e} — using fallback")

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
        return {"drug": drug, "labels": labels,
                        "baseline": baseline, "actual": actual,
                        "fallback": True, "note": "Using rolling average — train LSTM via POST /api/lstm/train"}, 200
    except Exception as e:
        return {"error": str(e)}, 500


def train_lstm(drug, epochs_raw):
    """Train a real PyTorch LSTM on openFDA historical data for a given drug."""
    if not LSTM_AVAILABLE:
        return {"error": "lstm_model.py not loaded"}, 500
    epochs = min(int(epochs_raw), 50)  # Capped at 50 to prevent DoS
    try:
        _, error = lstm_train(drug, epochs=epochs)
        if error:
            return {"error": error}, 400
        return {"status": "ok", "drug": drug,
                        "message": f"LSTM trained and saved for '{drug}'"}, 200
    except Exception as e:
        return {"error": str(e)}, 500


def lstm_gap_scan(drug):
    """Warning Gap Predictor (enhanced with ClinicalTrials.gov)."""
    if not LSTM_AVAILABLE:
        return {"error": "lstm_model.py not loaded"}, 500
    try:
        result = scan_warning_gap(drug)
        if "error" in result:
            return result, 200

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

        return result, 200
    except Exception as e:
        return {"error": str(e)}, 500
