# backend/services/feedback.py
# Reviewer feedback on a result (no Flask here). Stored with the result's data/model versions so a
# judgement is tied to the version it referred to; admin-exportable as pilot validation data.

import json

VERDICTS = ("correct", "incorrect", "unsure")


def submit(user, component, subject, verdict, comment, data_version=None, model_version=None):
    """Store one feedback row. Returns (payload, status)."""
    component = (component or "").strip()
    verdict = (verdict or "").strip().lower()
    if not component or verdict not in VERDICTS:
        return {"error": f"component is required and verdict must be one of {VERDICTS}"}, 400
    from db.mysql import execute
    try:
        execute(
            "INSERT INTO feedback (user_id, user_email, component, subject_json, verdict, comment, "
            "data_version, model_version) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            ((user or {}).get("id"), (user or {}).get("email"), component[:64],
             json.dumps(subject or {})[:4000], verdict, (comment or "")[:2000] or None,
             data_version, model_version))
    except Exception:
        return {"error": "Feedback store unavailable (MySQL is not reachable)."}, 503
    return {"ok": True, "verdict": verdict}, 201


def export_csv():
    import csv
    import io
    from db.mysql import query
    try:
        rows = query("SELECT id, at, user_email, component, subject_json, verdict, comment, "
                     "data_version, model_version FROM feedback ORDER BY id")
    except Exception:
        return None, ({"error": "Feedback store unavailable."}, 503)
    buf = io.StringIO()
    fields = ["id", "at", "user_email", "component", "subject_json", "verdict", "comment",
              "data_version", "model_version"]
    w = csv.DictWriter(buf, fieldnames=fields)
    w.writeheader()
    for r in rows:
        r["at"] = str(r["at"])
        w.writerow(r)
    return buf.getvalue(), None


def counts():
    from db.mysql import query
    try:
        rows = query("SELECT component, verdict, COUNT(*) AS n FROM feedback GROUP BY component, verdict")
    except Exception:
        return {}
    out = {}
    for r in rows:
        out.setdefault(r["component"], {})[r["verdict"]] = int(r["n"])
    return out
