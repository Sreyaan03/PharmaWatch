# backend/routes/audit.py
# HTTP layer for reading/exporting the audit log. Admin only.

from flask import Blueprint, Response, jsonify, request

from core.auth import require_role
from services import audit as svc

audit_bp = Blueprint("audit", __name__)


@audit_bp.route("/api/admin/audit", methods=["GET"])
@require_role("admin")
def list_audit():
    payload, status = svc.list_rows(limit=request.args.get("limit", 100),
                                    offset=request.args.get("offset", 0))
    return jsonify(payload), status


@audit_bp.route("/api/admin/audit/export", methods=["GET"])
@require_role("admin")
def export_audit():
    csv_text, error = svc.export_csv()
    if error is not None:
        payload, status = error
        return jsonify(payload), status
    return Response(csv_text, mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=audit_log.csv"})
