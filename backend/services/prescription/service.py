# backend/services/prescription/service.py
# The prescription review workflow on top of durable jobs:
#   upload -> extract (job "rx_extract") -> user confirms or corrects the list
#          -> review (job "rx_review") -> report (JSON, PDF)
# The extraction and the confirmed list are stored separately (job result vs job meta), and no
# report exists until a confirmed list does. Nothing is analysed from an unconfirmed extraction.

import functools
from datetime import datetime, timezone

from pymysql.err import Error as DbError

from core import jobs
from services.prescription import report as report_mod
from services.prescription import review as review_mod
from services.prescription.extract import CloudVisionExtractor, get_extractor, mime_for
from services.prescription.schema import LINE_FIELDS, MedicationLine, SchemaError

ALLOWED = {"pdf", "png", "jpg", "jpeg", "txt"}
RETENTION_MINUTES = 120
STORAGE_DOWN = ({"error": "Job storage is unavailable; try again shortly."}, 503)


def _storage_guard(fn):
    """Answer 503 instead of failing when the job store (MySQL) cannot be reached."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except DbError:
            if fn.__name__ == "report_pdf":
                return None, STORAGE_DOWN[0], STORAGE_DOWN[1]
            return STORAGE_DOWN
    return wrapper


def _identity_resolve(name):
    from services.signals import _identity
    try:
        return _identity().resolve_drug(name)
    except Exception:                          # identity lookup unavailable: left unresolved, not guessed
        return {"resolved": False, "ingredients": []}


def _extract_job(job, report):
    mime = job["meta"]["mime"]
    extractor = get_extractor()
    data = jobs.read_input(job)
    if data is None:
        raise ValueError("the upload is no longer available")
    result = extractor.extract(data, mime, report=report)
    out = result.to_dict()
    report("Matching names to ingredients...")
    for line in out["lines"]:                  # shown next to the transcribed text so the user can check
        r = _identity_resolve(line.get("ingredient") or line["drug_text"])
        line["resolution"] = {"resolved": bool(r.get("resolved")),
                              "ingredients": [i["name"] for i in r.get("ingredients", [])]}
    out["cloud_used"] = isinstance(extractor, CloudVisionExtractor)
    return out


def _review_job(job, report):
    parent = jobs.get(job["meta"]["parent"])
    if parent is None or not parent["meta"].get("confirmed"):
        raise ValueError("no confirmed list for this review")
    confirmed, extraction = parent["meta"]["confirmed"], parent["result"] or {}
    report("Checking interactions, warnings and signals...")
    from faers.registry import data_version
    from ml.ddi.inference import load_manifest
    manifest = load_manifest() or {}
    result = review_mod.review(confirmed["lines"], review_mod.default_deps())
    return report_mod.build_report(confirmed["lines"], result, {
        "reference": confirmed.get("reference"), "extractor": extraction.get("extractor"),
        "text_source": extraction.get("text_source"), "cloud_used": extraction.get("cloud_used"),
        "data_version": data_version(), "model_version": manifest.get("model_version")})


jobs.register("rx_extract", _extract_job)
jobs.register("rx_review", _review_job)


def upload(uploaded_file, reference=""):
    if uploaded_file is None or not (uploaded_file.filename or ""):
        return {"error": "Attach a file (PDF, PNG, JPG or TXT)."}, 400
    name = uploaded_file.filename
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext not in ALLOWED:
        return {"error": f"Unsupported file type '.{ext}'. Allowed: {', '.join(sorted(ALLOWED))}."}, 400
    data = uploaded_file.read()
    if not data:
        return {"error": "The file is empty."}, 400
    if len(data) > jobs.MAX_UPLOAD_BYTES:
        return {"error": "File exceeds maximum upload size (10 MB)"}, 413
    try:
        job_id = jobs.submit("rx_extract", data=data, filename=f"upload.{ext}",
                             meta={"mime": mime_for(name), "reference": (reference or "")[:80]},
                             ttl_minutes=RETENTION_MINUTES)
    except Exception:
        return {"error": "Job storage is unavailable; try again shortly."}, 503
    return {"job_id": job_id, "uploads_kept": "only while the extraction runs; deleted when it finishes"}, 202


@_storage_guard
def status(job_id):
    job = jobs.get(job_id)
    if job is None or job["kind"] != "rx_extract":
        return {"error": "Not found (it may have expired)."}, 404
    body = {"job_id": job_id, "status": job["status"], "progress": job["progress"], "error": job["error"],
            "confirmed": bool(job["meta"].get("confirmed")), "reference": job["meta"].get("reference")}
    if job["status"] == "done":
        body["extraction"] = job["result"]
    return body, 200


def _clean_line(raw):
    if not isinstance(raw, dict):
        raise SchemaError("each line must be an object")
    kept = {k: raw[k] for k in LINE_FIELDS if k in raw}
    try:
        line = MedicationLine(**kept)
        line.confidence = float(line.confidence)
    except (TypeError, ValueError) as exc:
        raise SchemaError(str(exc)) from exc
    line.validate()
    return {k: getattr(line, k) for k in LINE_FIELDS}


@_storage_guard
def confirm(job_id, body):
    job = jobs.get(job_id)
    if job is None or job["kind"] != "rx_extract":
        return {"error": "Not found (it may have expired)."}, 404
    if job["status"] != "done":
        return {"error": "The extraction has not finished."}, 409
    raw_lines = (body or {}).get("lines")
    if not isinstance(raw_lines, list) or not raw_lines:
        return {"error": "Send the confirmed medication list as {'lines': [...]}."}, 400
    try:
        lines = [_clean_line(l) for l in raw_lines]
    except SchemaError as exc:
        return {"error": f"Invalid line: {exc}"}, 400
    reference = ((body or {}).get("reference") or job["meta"].get("reference") or "")[:80]
    try:
        review_id = jobs.submit("rx_review", meta={"parent": job_id}, ttl_minutes=RETENTION_MINUTES)
    except Exception:
        return {"error": "Job storage is unavailable; try again shortly."}, 503
    jobs.update_meta(job_id, {"confirmed": {"lines": lines, "reference": reference,
                                            "confirmed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")},
                              "review_job_id": review_id})
    return {"job_id": job_id, "confirmed_lines": len(lines), "report_status": "running"}, 202


@_storage_guard
def report(job_id):
    """(payload, status). 409 until a confirmed list exists; 202 while the review runs."""
    job = jobs.get(job_id)
    if job is None or job["kind"] != "rx_extract":
        return {"error": "Not found (it may have expired)."}, 404
    if not job["meta"].get("confirmed"):
        return {"error": "Confirm or correct the medication list first; no report is produced from an "
                         "unconfirmed extraction."}, 409
    review_job = jobs.get(job["meta"].get("review_job_id") or "")
    if review_job is None:
        return {"error": "The report has expired; confirm the list again."}, 404
    if review_job["status"] in ("queued", "running"):
        return {"status": "running", "progress": review_job["progress"]}, 202
    if review_job["status"] == "failed":
        return {"error": f"The review failed: {review_job['error']}"}, 500
    return review_job["result"], 200


@_storage_guard
def report_pdf(job_id):
    """(pdf bytes | None, error payload | None, status)."""
    payload, code = report(job_id)
    if code != 200:
        return None, payload, code
    return report_mod.render_pdf(payload), None, 200


@_storage_guard
def delete(job_id):
    job = jobs.get(job_id)
    if job is None:
        return {"deleted": False}, 404
    for jid in (job["meta"].get("review_job_id"), job_id):
        if jid:
            jobs.delete(jid)
    return {"deleted": True}, 200
