# backend/routes/prescription.py
# HTTP layer only: parse the request, call the service, return JSON or a PDF.

from flask import Blueprint, Response, jsonify, request
from services.prescription import service as svc

prescription_bp = Blueprint("prescription", __name__)


@prescription_bp.route("/api/prescription/config", methods=["GET"])
def config():
    """Which extractor will run, whether the document leaves this machine, and the limits."""
    payload, code = svc.config()
    return jsonify(payload), code


@prescription_bp.route("/api/prescription", methods=["POST"])
def upload():
    """Upload a prescription or discharge summary (PDF/PNG/JPG/TXT); returns a job id."""
    payload, status = svc.upload(request.files.get("file"), request.form.get("reference", ""))
    return jsonify(payload), status


@prescription_bp.route("/api/prescription/<job_id>", methods=["GET"])
def status(job_id):
    """Extraction status and, when done, the transcribed medication lines to confirm."""
    payload, code = svc.status(job_id)
    return jsonify(payload), code


@prescription_bp.route("/api/prescription/<job_id>/confirm", methods=["POST"])
def confirm(job_id):
    """The user's confirmed or corrected medication list; starts the review."""
    payload, code = svc.confirm(job_id, request.get_json(silent=True))
    return jsonify(payload), code


@prescription_bp.route("/api/prescription/<job_id>/report", methods=["GET"])
def report(job_id):
    """The review report (409 until a list is confirmed, 202 while it runs)."""
    payload, code = svc.report(job_id)
    return jsonify(payload), code


@prescription_bp.route("/api/prescription/<job_id>/report.pdf", methods=["GET"])
def report_pdf(job_id):
    """The same report as a PDF."""
    pdf, error, code = svc.report_pdf(job_id)
    if pdf is None:
        return jsonify(error), code
    return Response(pdf, mimetype="application/pdf",
                    headers={"Content-Disposition": "attachment; filename=prescription-review.pdf"})


@prescription_bp.route("/api/prescription/<job_id>", methods=["DELETE"])
def delete(job_id):
    """Erase the job and its report now instead of waiting for it to expire."""
    payload, code = svc.delete(job_id)
    return jsonify(payload), code
