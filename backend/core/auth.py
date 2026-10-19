# backend/core/auth.py
# Request guards shared by the route modules.

from functools import wraps

from flask import jsonify, request

from core import config


def require_admin(view):
    """Reject the request unless it carries the configured admin key.

    403 when ADMIN_API_KEY is not configured, 401 on a missing or wrong key.
    """
    @wraps(view)
    def wrapper(*args, **kwargs):
        admin_key = config.get("ADMIN_API_KEY")
        if not admin_key:
            return jsonify({"error": "Admin endpoints disabled. Configure ADMIN_API_KEY in .env"}), 403
        provided = request.headers.get("X-Admin-Key") or request.args.get("admin_key")
        if not provided or provided.strip() != admin_key:
            return jsonify({"error": "Unauthorized: invalid or missing admin key"}), 401
        return view(*args, **kwargs)
    return wrapper
