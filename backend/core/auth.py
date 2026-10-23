# backend/core/auth.py
# Accounts, roles and the request guards shared by the route modules.
#
# Roles rank viewer < analyst < admin. A guarded view allows the named role and anything above it.
# When AUTH_REQUIRED is off (single-user local development) the guards pass through and the UI shows
# a banner; when it is on, sign-in is required and roles are enforced. Authentication goes through a
# replaceable AuthProvider, so single sign-on can be added later without touching the routes.

from functools import wraps

from flask import jsonify, request, session

from core import config

ROLES = ("viewer", "analyst", "admin")
_RANK = {r: i for i, r in enumerate(ROLES)}


def auth_enabled():
    return bool(config.get("AUTH_REQUIRED"))


# ── provider interface ────────────────────────────────────────────────────────
class AuthProvider:
    """Replaceable authentication backend."""

    def authenticate(self, email, password):
        """Return a user dict {id, email, role, organisation} on success, else None."""
        raise NotImplementedError


class LocalAuthProvider(AuthProvider):
    """Accounts stored in the `users` table; passwords are Werkzeug hashes."""

    def authenticate(self, email, password):
        from werkzeug.security import check_password_hash

        from db.mysql import execute, query_one
        if not email or not password:
            return None
        row = query_one("SELECT id, email, password_hash, role, organisation, active "
                        "FROM users WHERE email = %s", (email.strip().lower(),))
        if not row or not row.get("active") or not check_password_hash(row["password_hash"], password):
            return None
        try:
            execute("UPDATE users SET last_login = NOW() WHERE id = %s", (row["id"],))
        except Exception:
            pass
        return {"id": row["id"], "email": row["email"], "role": row["role"],
                "organisation": row.get("organisation")}


_provider = None


def provider():
    global _provider
    if _provider is None:
        _provider = LocalAuthProvider()          # the only implementation today
    return _provider


# ── session helpers ───────────────────────────────────────────────────────────
def login_user(user):
    session.clear()
    session["uid"] = user["id"]
    session["email"] = user["email"]
    session["role"] = user["role"]
    session.permanent = True


def logout_user():
    session.clear()


def current_user():
    """The signed-in user from the session, or None. When auth is off, returns None (open mode)."""
    if not session.get("uid"):
        return None
    return {"id": session["uid"], "email": session.get("email"), "role": session.get("role", "viewer")}


def current_role():
    u = current_user()
    return u["role"] if u else None


# ── guards ────────────────────────────────────────────────────────────────────
def require_login(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if auth_enabled() and not current_user():
            return jsonify({"error": "Sign in required.", "auth": "required"}), 401
        return view(*args, **kwargs)
    return wrapper


def require_role(role):
    """Allow the named role and anything above it. Open when AUTH_REQUIRED is off."""
    needed = _RANK[role]

    def decorator(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            if not auth_enabled():
                return view(*args, **kwargs)
            user = current_user()
            if not user:
                return jsonify({"error": "Sign in required.", "auth": "required"}), 401
            if _RANK.get(user["role"], -1) < needed:
                return jsonify({"error": f"This action needs the {role} role."}), 403
            return view(*args, **kwargs)
        return wrapper
    return decorator


def require_admin(view):
    """Admin-key guard. The key is accepted in the X-Admin-Key header ONLY (never a query
    parameter, so it cannot leak into logs or the audit trail) and compared in constant time.

    403 when ADMIN_API_KEY is not configured, 401 on a missing or wrong key.
    """
    import hmac

    @wraps(view)
    def wrapper(*args, **kwargs):
        admin_key = config.get("ADMIN_API_KEY")
        if not admin_key:
            return jsonify({"error": "Admin endpoints disabled. Configure ADMIN_API_KEY in .env"}), 403
        provided = (request.headers.get("X-Admin-Key") or "").strip()
        if not provided or not hmac.compare_digest(provided, admin_key):
            return jsonify({"error": "Unauthorized: invalid or missing admin key"}), 401
        return view(*args, **kwargs)
    return wrapper
