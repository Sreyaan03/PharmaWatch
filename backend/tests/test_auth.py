"""
Accounts and roles: passwords are hashed (never clear text), sign-in works, and when
AUTH_REQUIRED is on a viewer cannot reach an analyst-only action while an analyst can. With auth
off, the guards pass through so local development stays open.
"""

import os
import sys

import pytest

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from app import app  # noqa: E402
from core import auth  # noqa: E402


def _mysql_ready():
    try:
        from db.mysql import query_one
        return query_one("SELECT COUNT(*) AS n FROM users") is not None
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _mysql_ready(), reason="MySQL with the users table is not reachable")

VIEWER = "uat_viewer@test.edu"
ANALYST = "uat_analyst@test.edu"
PW = "pilot-pass-123"


@pytest.fixture
def users():
    from werkzeug.security import generate_password_hash

    from db.mysql import execute
    execute("DELETE FROM users WHERE email IN (%s, %s)", (VIEWER, ANALYST))
    execute("INSERT INTO users (email, password_hash, role) VALUES (%s, %s, 'viewer')",
            (VIEWER, generate_password_hash(PW)))
    execute("INSERT INTO users (email, password_hash, role) VALUES (%s, %s, 'analyst')",
            (ANALYST, generate_password_hash(PW)))
    yield
    execute("DELETE FROM users WHERE email IN (%s, %s)", (VIEWER, ANALYST))


def test_password_is_hashed_not_plaintext(users):
    from db.mysql import query_one
    from werkzeug.security import check_password_hash
    row = query_one("SELECT password_hash FROM users WHERE email = %s", (ANALYST,))
    assert row["password_hash"] != PW and PW not in row["password_hash"]
    assert check_password_hash(row["password_hash"], PW)


def test_login_me_and_bad_password(users):
    client = app.test_client()
    assert client.post("/api/auth/login", json={"email": ANALYST, "password": "wrong"}).status_code == 401
    r = client.post("/api/auth/login", json={"email": ANALYST, "password": PW})
    assert r.status_code == 200 and r.get_json()["user"]["role"] == "analyst"
    me = client.get("/api/auth/me")
    assert me.status_code == 200 and me.get_json()["user"]["email"] == ANALYST


def test_role_enforced_when_auth_on(users, monkeypatch):
    monkeypatch.setattr(auth, "auth_enabled", lambda: True)

    anon = app.test_client()
    assert anon.post("/api/ner", json={"text": "x"}).status_code == 401      # not signed in

    viewer = app.test_client()
    viewer.post("/api/auth/login", json={"email": VIEWER, "password": PW})
    assert viewer.post("/api/ner", json={"text": "x"}).status_code == 403    # analyst-only

    analyst = app.test_client()
    analyst.post("/api/auth/login", json={"email": ANALYST, "password": PW})
    assert analyst.post("/api/ner", json={"text": "x"}).status_code not in (401, 403)


def test_guards_open_when_auth_off(monkeypatch):
    monkeypatch.setattr(auth, "auth_enabled", lambda: False)
    # no login; an analyst-only route is not blocked by auth (may fail for other reasons, not 401/403)
    assert app.test_client().post("/api/ner", json={"text": "x"}).status_code not in (401, 403)


def test_status_reports_auth_mode():
    d = app.test_client().get("/api/auth/status").get_json()
    assert "auth_required" in d and "user" in d
