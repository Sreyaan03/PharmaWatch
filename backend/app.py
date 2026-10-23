# backend/app.py
# ─────────────────────────────────────────────────────────────────────────────
# PharmaWatch Backend Entry Point (Refactored Blueprint Architecture)
# ─────────────────────────────────────────────────────────────────────────────

import logging
import pathlib
import socket
from flask import Flask, send_from_directory
from flask_cors import CORS
from core import config

from database import init_db

# Request-time diagnostics go through logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

# Load backend/.env (existing environment variables win)
config.load_env()

# Initialize Database
init_db()

# Resolve path for static frontend files (D3 UI)
SRC_DIR = str(pathlib.Path(__file__).parent.parent / "src")

# Initialize Flask Application
app = Flask(__name__, static_folder=SRC_DIR, static_url_path='/static')

# Set up CORS based on configured origins (credentials on, so the session cookie is sent)
_allowed_origins = config.get("ALLOWED_ORIGINS").split(",")
CORS(app, origins=_allowed_origins, supports_credentials=True)

# Session cookie: signed with FLASK_SECRET_KEY. When AUTH_REQUIRED is on, a secret is mandatory;
# in open local development a random per-process key is fine (sessions just do not survive restarts).
import secrets as _secrets  # noqa: E402
_secret = config.get("FLASK_SECRET_KEY")
if not _secret:
    if config.get("AUTH_REQUIRED"):
        raise RuntimeError("AUTH_REQUIRED is on but FLASK_SECRET_KEY is not set; configure it in .env")
    _secret = _secrets.token_hex(32)
app.secret_key = _secret
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=bool(config.get("SESSION_COOKIE_SECURE")),
    # Reject an oversized request body before any handler reads it (returns 413).
    MAX_CONTENT_LENGTH=int(config.get("MAX_UPLOAD_MB")) * 1024 * 1024,
)

# Generic error responses (no raw exception text reaches the client).
from core.errors import register_error_handlers  # noqa: E402
register_error_handlers(app)

# Rate limiter (shared via core.limiter so routes can add per-endpoint limits). No-op if the package
# is not installed. Default limit and storage come from configuration.
from core.limiter import limiter  # noqa: E402
app.config["RATELIMIT_DEFAULT"] = config.get("RATE_LIMIT_DEFAULT")
app.config["RATELIMIT_STORAGE_URI"] = config.get("RATE_LIMIT_STORAGE_URI")
limiter.init_app(app)

# Serve index.html (ensure no caching for latest static asset updates)
@app.route("/")
@limiter.exempt  # Exclude frontend index from rate limiting
def serve_index():
    """Serve the PharmaWatch frontend."""
    from flask import make_response
    resp = make_response(send_from_directory(SRC_DIR, "index.html"))
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    resp.headers['Pragma'] = 'no-cache'
    resp.headers['Expires'] = '0'
    return resp

_INSTANCE = socket.gethostname()


@app.before_request
def _assign_request_id():
    import uuid

    from flask import g
    g.request_id = uuid.uuid4().hex


@app.after_request
def _audit(response):
    """Record auditable requests. Flask context is read here; the DB write is in services.audit."""
    from flask import g, request
    from core.auth import current_user
    from services import audit as audit_svc
    try:
        if audit_svc.should_audit(request.path, request.method):
            rule = request.url_rule.rule if request.url_rule else request.path
            form = {}
            try:
                form = dict(request.form)
            except Exception:
                form = {}
            audit_svc.write_row(current_user(), request.path, rule, request.method,
                                dict(request.args), form, response.status_code,
                                getattr(g, "request_id", None))
    except Exception:
        pass
    return response


@app.after_request
def name_the_instance(response):
    """Say which copy answered, so load balancing can be seen (and debugged) from outside."""
    response.headers["X-Instance"] = _INSTANCE
    return response


@app.after_request
def attach_provenance(response):
    """Add `meta` (evidence kind, data version, generated_at) to JSON object responses."""
    import json
    from flask import request
    from core.response import with_meta
    from services.provenance import current_data_version, describe

    if (response.status_code >= 400 or response.direct_passthrough
            or response.mimetype != "application/json"):
        return response
    kind = describe(request.path)
    if kind is None:
        return response
    try:
        payload = json.loads(response.get_data(as_text=True))
    except ValueError:
        return response
    evidence, model_version, source = kind
    data_version = {"faers_local": current_data_version, "openfda_live": lambda: "openFDA (live query)"}.get(source)
    annotated = with_meta(payload, evidence, data_version() if data_version else None, model_version)
    if annotated is not payload:
        response.set_data(json.dumps(annotated))
    return response


# Register Blueprints
from routes.general import general_bp
from routes.signals import signals_bp
from routes.interactions import interactions_bp
from routes.boxed_warnings import boxed_warnings_bp
from routes.ml import ml_bp
from routes.dashboard import dashboard_bp
from routes.analytics import analytics_bp
from routes.temporal import temporal_bp
from routes.demographics import demographics_bp
from routes.pipeline import pipeline_bp
from routes.validation import validation_bp
from routes.prescription import prescription_bp
from routes.surveillance import surveillance_bp
from routes.drug import drug_bp
from routes.auth import auth_bp
from routes.audit import audit_bp
from routes.feedback import feedback_bp

app.register_blueprint(general_bp)
app.register_blueprint(signals_bp)
app.register_blueprint(interactions_bp)
app.register_blueprint(boxed_warnings_bp)
app.register_blueprint(ml_bp)
app.register_blueprint(dashboard_bp)
app.register_blueprint(analytics_bp)
app.register_blueprint(temporal_bp)
app.register_blueprint(demographics_bp)
app.register_blueprint(pipeline_bp)
app.register_blueprint(validation_bp)
app.register_blueprint(prescription_bp)
app.register_blueprint(surveillance_bp)
app.register_blueprint(drug_bp)
app.register_blueprint(auth_bp)
app.register_blueprint(audit_bp)
app.register_blueprint(feedback_bp)

def _job_housekeeping():
    """Inline mode: jobs left by the previous process are requeued here (workers do this in kafka
    mode, where other processes may still be running them). Expired jobs are removed."""
    try:
        from core import jobs, queue
        if queue.mode() == "inline":
            jobs.recover_interrupted(startup=True)
        jobs.purge_expired()
    except Exception as exc:                      # the app runs without the job store
        logging.getLogger(__name__).warning(f"[JOBS] startup housekeeping skipped: {exc}")


_job_housekeeping()

if __name__ == "__main__":
    app.run(host=config.get("HOST"), port=config.get("PORT"), debug=False)
