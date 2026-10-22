# backend/routes/feedback.py
# HTTP layer for feedback capture and admin export.

from flask import Blueprint, Response, jsonify, request

from core.auth import current_user, require_login, require_role
from services import feedback as svc

feedback_bp = Blueprint("feedback", __name__)


@feedback_bp.route("/api/feedback", methods=["POST"])
@require_login
def submit():
    body = request.get_json(silent=True) or {}
    payload, status = svc.submit(
        current_user(), body.get("component"), body.get("subject"), body.get("verdict"),
        body.get("comment"),
        data_version=(body.get("meta") or {}).get("data_version"),
        model_version=(body.get("meta") or {}).get("model_version"))
    return jsonify(payload), status


@feedback_bp.route("/api/feedback/summary", methods=["GET"])
@require_role("admin")
def summary():
    return jsonify({"counts": svc.counts()}), 200


@feedback_bp.route("/api/feedback/export", methods=["GET"])
@require_role("admin")
def export():
    csv_text, error = svc.export_csv()
    if error is not None:
        payload, status = error
        return jsonify(payload), status
    return Response(csv_text, mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=feedback.csv"})
