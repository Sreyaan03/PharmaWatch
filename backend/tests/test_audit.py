"""
The audit log records analyses, logins and admin actions with the user and the data/model versions,
never records secrets or document content, and the code holds no UPDATE/DELETE path on the table.
"""

import os
import sys

import pytest

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from app import app  # noqa: E402
from services import audit  # noqa: E402


def _mysql_ready():
    try:
        from db.mysql import query_one
        return query_one("SELECT COUNT(*) AS n FROM audit_log") is not None
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _mysql_ready(), reason="MySQL with the audit_log table is not reachable")


def test_versions_are_captured_per_route():
    # a measured FAERS route carries a data version; a modelled route carries a model version
    dv, mv = audit._versions("/api/signals")
    assert dv and mv is None
    dv2, mv2 = audit._versions("/api/lstm")
    assert mv2 == "lstm-forecast"


def test_login_is_audited_without_the_password():
    from db.mysql import execute, query_one
    execute("DELETE FROM audit_log WHERE route = '/api/auth/login' AND user_email IS NULL")
    app.test_client().post("/api/auth/login", json={"email": "ghost@test.edu", "password": "supersecret"})
    row = query_one("SELECT action, inputs_json, status FROM audit_log "
                    "WHERE route = '/api/auth/login' ORDER BY id DESC LIMIT 1")
    assert row and row["action"] == "login"
    assert "supersecret" not in (row["inputs_json"] or "") and "password" not in (row["inputs_json"] or "")


def test_an_analysis_writes_a_row_with_the_data_version():
    from db.mysql import query_one
    app.test_client().get("/api/local-stats")
    row = query_one("SELECT action, data_version FROM audit_log WHERE route = '/api/local-stats' "
                    "ORDER BY id DESC LIMIT 1")
    assert row and row["action"] == "analysis" and row["data_version"]


def test_a_non_auditable_request_writes_nothing():
    from db.mysql import query_one
    before = query_one("SELECT COUNT(*) AS n FROM audit_log")["n"]
    app.test_client().get("/api/auth/status")      # status is not an auditable action
    after = query_one("SELECT COUNT(*) AS n FROM audit_log")["n"]
    assert after == before


def test_code_has_no_update_or_delete_on_audit_log():
    import re
    text = open(os.path.join(BACKEND, "services", "audit.py"), encoding="utf-8").read().lower()
    assert not re.search(r"(update|delete)\s+.*audit_log", text)
    assert not re.search(r"audit_log\s+set", text)
