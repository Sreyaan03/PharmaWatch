"""
Security hardening: the admin key is accepted in the header only (never a query parameter), an
internal error returns a generic body with no exception text, and an oversized request body is
rejected with 413 before a handler runs.
"""

import os
import sys

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from app import app  # noqa: E402


def _with_admin_key(monkeypatch, key="s3cr3t-key"):
    from core import auth as auth_mod
    real = auth_mod.config.get
    monkeypatch.setattr(auth_mod.config, "get", lambda name: key if name == "ADMIN_API_KEY" else real(name))


# A bare view guarded by require_admin, so the guard is tested without running a heavy endpoint.
def _guarded():
    from core.auth import require_admin

    @require_admin
    def view():
        return "ok"
    return view


def test_admin_key_query_param_is_rejected(monkeypatch):
    _with_admin_key(monkeypatch)
    with app.test_request_context("/x?admin_key=s3cr3t-key", method="POST"):
        result = _guarded()()
        assert isinstance(result, tuple) and result[1] == 401   # query key does not authenticate


def test_admin_key_header_is_accepted(monkeypatch):
    _with_admin_key(monkeypatch)
    with app.test_request_context("/x", method="POST", headers={"X-Admin-Key": "s3cr3t-key"}):
        assert _guarded()() == "ok"                              # header key passes the guard


def test_internal_error_returns_no_exception_text():
    # a throwaway app with the real handlers, so the shared app's routes are not touched
    from flask import Flask
    from core.errors import register_error_handlers
    a = Flask(__name__)

    @a.route("/boom")
    def _boom():
        raise RuntimeError("internal detail should never leak to a client")
    register_error_handlers(a)
    r = a.test_client().get("/boom")
    assert r.status_code == 500
    body = r.get_data(as_text=True)
    assert "internal detail" not in body and "Traceback" not in body and "RuntimeError" not in body
    data = r.get_json()
    assert data and "request_id" in data and "error" in data


def test_oversized_body_returns_413(monkeypatch):
    monkeypatch.setitem(app.config, "MAX_CONTENT_LENGTH", 500)
    r = app.test_client().post("/api/feedback", data=b"x" * 2000, content_type="application/json")
    assert r.status_code == 413


def test_fda_cache_table_is_not_in_the_schema():
    schema = open(os.path.join(BACKEND, "db", "schema.sql"), encoding="utf-8").read()
    assert "CREATE TABLE IF NOT EXISTS fda_cache" not in schema
