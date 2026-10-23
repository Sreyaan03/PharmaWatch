# backend/core/errors.py
# One place that turns any unhandled error into a generic client response with a request id, while
# the real detail is logged. Clients never receive raw exception text or a traceback.

import logging

from flask import g, jsonify, request
from werkzeug.exceptions import HTTPException

log = logging.getLogger("errors")

_GENERIC = {
    400: "Bad request.",
    401: "Sign in required.",
    403: "Not allowed.",
    404: "Not found.",
    405: "Method not allowed.",
    413: "Upload too large.",
    429: "Too many requests; slow down.",
    500: "Something went wrong. Please try again; if it persists, quote the request id.",
    503: "A dependency is unavailable; try again shortly.",
}


def _request_id():
    return getattr(g, "request_id", None)


def register_error_handlers(app):
    @app.errorhandler(HTTPException)
    def _http(exc):
        # Keep the status, but replace the body with a generic message (no werkzeug description leak).
        code = exc.code or 500
        return jsonify({"error": _GENERIC.get(code, exc.name), "request_id": _request_id()}), code

    @app.errorhandler(Exception)
    def _unhandled(exc):
        # Log the real detail; return a generic 500 with the request id for support.
        log.error(f"[{_request_id()}] unhandled error on {request.method} {request.path}: {exc}",
                  exc_info=True)
        return jsonify({"error": _GENERIC[500], "request_id": _request_id()}), 500
