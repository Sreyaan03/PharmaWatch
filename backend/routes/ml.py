# backend/routes/ml.py
# HTTP layer only: parse the request, call the service, return JSON.

from core.auth import require_admin
from flask import Blueprint, request, jsonify
from services import forecasting as svc

ml_bp = Blueprint("ml", __name__)

@ml_bp.route("/api/lstm", methods=["GET"])
def get_lstm_forecast():
    """
    Returns real LSTM predictions vs actual FAERS counts.
    If model not yet trained, falls back to the rolling average.
    """
    drug = request.args.get('drug', 'Metformin')
    payload, status = svc.get_lstm_forecast(drug)
    return jsonify(payload), status


@ml_bp.route("/api/lstm/train", methods=["POST"])
@require_admin
def train_lstm():
    """Train a real PyTorch LSTM on openFDA historical data for a given drug."""
    drug = request.args.get('drug', 'Metformin')
    epochs_raw = request.args.get('epochs', 30)
    payload, status = svc.train_lstm(drug, epochs_raw)
    return jsonify(payload), status


@ml_bp.route("/api/lstm/gap-scan", methods=["GET"])
def lstm_gap_scan():
    """Warning Gap Predictor (enhanced with ClinicalTrials.gov)."""
    drug = request.args.get('drug', 'Metformin')
    payload, status = svc.lstm_gap_scan(drug)
    return jsonify(payload), status
