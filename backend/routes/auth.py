# backend/routes/auth.py
# HTTP layer for sign-in. No self-registration: accounts are created with core.manage.

from flask import Blueprint, jsonify, request

from core import auth

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/api/auth/status", methods=["GET"])
def status():
    """Whether sign-in is required and who is signed in (drives the login UI and the banner)."""
    return jsonify({"auth_required": auth.auth_enabled(), "user": auth.current_user()}), 200


@auth_bp.route("/api/auth/login", methods=["POST"])
def login():
    body = request.get_json(silent=True) or {}
    email = (body.get("email") or "").strip()
    password = body.get("password") or ""
    try:
        user = auth.provider().authenticate(email, password)
    except Exception:
        return jsonify({"error": "Account store unavailable."}), 503
    if not user:
        return jsonify({"error": "Invalid email or password."}), 401
    auth.login_user(user)
    return jsonify({"user": auth.current_user()}), 200


@auth_bp.route("/api/auth/logout", methods=["POST"])
def logout():
    auth.logout_user()
    return jsonify({"ok": True}), 200


@auth_bp.route("/api/auth/me", methods=["GET"])
@auth.require_login
def me():
    return jsonify({"user": auth.current_user()}), 200
