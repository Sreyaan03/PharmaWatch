# backend/services/audit.py
# Append-only audit trail (no Flask here — the request context is passed in from app.py).
#
# One row per analysis, upload, confirmation, export, login and admin action, with the user, the
# input identities/parameters (never document content or free text) and the data/model versions.
# The code writes only INSERT/SELECT on audit_log; there is no UPDATE or DELETE path.

import json
import logging

log = logging.getLogger("audit")

# Form fields safe to record: identities and parameters, never secrets or document text.
_SAFE_FORM_KEYS = {"drug", "event", "reference", "quarter", "status", "role", "email",
                   "granularity", "limit", "from", "to", "source"}
_NEVER = {"password", "text", "file"}


def should_audit(path, method):
    """Which requests are recorded: analytic results, uploads, confirmations, exports, auth, admin."""
    if path.startswith("/api/admin"):
        return True
    if path in ("/api/auth/login", "/api/auth/logout"):
        return True
    if "/export" in path:
        return True
    if method == "POST" and (path.startswith("/api/prescription") or path.startswith("/api/ner")
                             or path.startswith("/api/signals/ner-mine") or path.startswith("/api/feedback")):
        return True
    from services.provenance import describe
    kind = describe(path)
    return bool(kind and kind[0] in ("measured", "modelled", "composite"))


def action_for(path, method):
    if path.startswith("/api/auth/login"):
        return "login"
    if path.startswith("/api/auth/logout"):
        return "logout"
    if path.startswith("/api/admin"):
        return "admin"
    if "/export" in path:
        return "export"
    if path.startswith("/api/prescription"):
        return "prescription_confirm" if path.endswith("/confirm") else "prescription_upload"
    if path.startswith("/api/ner") or path.startswith("/api/signals/ner-mine"):
        return "text_mining"
    if path.startswith("/api/feedback"):
        return "feedback"
    return "analysis"


def sanitize_inputs(args, form):
    """Identities/parameters only; secrets and document text are dropped."""
    data = {k: v for k, v in (args or {}).items() if k.lower() not in _NEVER}
    for k, v in (form or {}).items():
        if k in _SAFE_FORM_KEYS and k.lower() not in _NEVER:
            data[k] = v
    return data


def _versions(path):
    from services.provenance import current_data_version, describe
    kind = describe(path)
    if not kind:
        return None, None
    _evidence, model_version, source = kind
    dv = (current_data_version() if source == "faers_local"
          else "openFDA (live query)" if source == "openfda_live" else None)
    return dv, model_version


def write_row(user, path, rule, method, args, form, status, request_id):
    """Insert one audit row. Never raises into the caller (the response must still be returned)."""
    try:
        from db.mysql import execute
        dv, mv = _versions(path)
        execute(
            "INSERT INTO audit_log (user_id, user_email, action, route, method, inputs_json, "
            "data_version, model_version, status, request_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            ((user or {}).get("id"), (user or {}).get("email"), action_for(path, method), rule, method,
             json.dumps(sanitize_inputs(args, form))[:4000], dv, mv, status, request_id))
    except Exception as exc:
        log.warning(f"[AUDIT] could not record {path}: {exc}")


def list_rows(limit=100, offset=0):
    from db.mysql import query
    try:
        rows = query("SELECT id, at, user_email, action, route, method, inputs_json, data_version, "
                     "model_version, status FROM audit_log ORDER BY id DESC LIMIT %s OFFSET %s",
                     (min(int(limit), 500), max(int(offset), 0)))
    except Exception:
        return {"error": "Audit store unavailable.", "rows": []}, 503
    for r in rows:
        r["at"] = str(r["at"])
    return {"rows": rows, "count": len(rows), "offset": int(offset)}, 200


def export_csv():
    import csv
    import io
    from db.mysql import query
    try:
        rows = query("SELECT id, at, user_email, action, route, method, inputs_json, data_version, "
                     "model_version, status, request_id FROM audit_log ORDER BY id")
    except Exception:
        return None, ({"error": "Audit store unavailable."}, 503)
    buf = io.StringIO()
    fields = ["id", "at", "user_email", "action", "route", "method", "inputs_json",
              "data_version", "model_version", "status", "request_id"]
    w = csv.DictWriter(buf, fieldnames=fields)
    w.writeheader()
    for r in rows:
        r["at"] = str(r["at"])
        w.writerow(r)
    return buf.getvalue(), None
