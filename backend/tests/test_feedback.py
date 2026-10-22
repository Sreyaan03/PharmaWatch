"""
Feedback is captured with the result's data/model versions, rejects an invalid verdict, and exports
one row per submission.
"""

import os
import sys

import pytest

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from app import app  # noqa: E402


def _mysql_ready():
    try:
        from db.mysql import query_one
        return query_one("SELECT COUNT(*) AS n FROM feedback") is not None
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _mysql_ready(), reason="MySQL with the feedback table is not reachable")

COMPONENT = "uat_test_signal"


@pytest.fixture
def clean():
    from db.mysql import execute
    execute("DELETE FROM feedback WHERE component = %s", (COMPONENT,))
    yield
    execute("DELETE FROM feedback WHERE component = %s", (COMPONENT,))


def _post(client, verdict="correct"):
    return client.post("/api/feedback", json={
        "component": COMPONENT, "subject": {"drug": "Metformin", "event": "Lactic acidosis"},
        "verdict": verdict, "comment": "looks right",
        "meta": {"data_version": "FAERS test v1", "model_version": None}})


def test_feedback_stored_with_versions(clean):
    from db.mysql import query_one
    r = _post(app.test_client())
    assert r.status_code == 201
    row = query_one("SELECT verdict, data_version, subject_json FROM feedback WHERE component = %s "
                    "ORDER BY id DESC LIMIT 1", (COMPONENT,))
    assert row["verdict"] == "correct" and row["data_version"] == "FAERS test v1"
    assert "Metformin" in (row["subject_json"] or "")


def test_invalid_verdict_rejected(clean):
    assert _post(app.test_client(), verdict="maybe").status_code == 400


def test_export_one_row_per_submission(clean):
    client = app.test_client()
    _post(client, "correct")
    _post(client, "incorrect")
    text = client.get("/api/feedback/export").get_data(as_text=True)
    ours = [ln for ln in text.splitlines() if COMPONENT in ln]
    assert len(ours) == 2
