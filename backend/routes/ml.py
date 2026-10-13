# backend/routes/ml.py
# HTTP layer only: parse the request, call the service, return JSON.

import os
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
def train_lstm():
    """Train a real PyTorch LSTM on openFDA historical data for a given drug."""
    admin_key = os.getenv("ADMIN_API_KEY", "").strip()
    if not admin_key:
        return jsonify({"error": "Admin endpoints disabled. Configure ADMIN_API_KEY in .env"}), 403
    provided = request.headers.get("X-Admin-Key") or request.args.get("admin_key")
    if not provided or provided.strip() != admin_key:
        return jsonify({"error": "Unauthorized: invalid or missing admin key"}), 401

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
