# backend/app.py
# ─────────────────────────────────────────────────────────────────────────────
# PharmaWatch Backend Entry Point (Refactored Blueprint Architecture)
# ─────────────────────────────────────────────────────────────────────────────

import os
import pathlib
import sys
from flask import Flask, send_from_directory
from flask_cors import CORS
from dotenv import load_dotenv

# Ensure backend directory is in sys.path
backend_dir = os.path.dirname(os.path.abspath(__file__))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from database import init_db

# Load environment variables
load_dotenv()

# Initialize Database
init_db()

# Resolve path for static frontend files (D3 UI)
SRC_DIR = str(pathlib.Path(__file__).parent.parent / "src")

# Initialize Flask Application
app = Flask(__name__, static_folder=SRC_DIR, static_url_path='/static')

# Set up CORS based on configured origins
_allowed_origins = os.getenv(
    'ALLOWED_ORIGINS',
    'http://localhost:5000,http://127.0.0.1:5000'
).split(',')
CORS(app, origins=_allowed_origins)

# Initialize Rate Limiter with a graceful fallback if not installed globally
try:
    from flask_limiter import Limiter
    from flask_limiter.util import get_remote_address
    limiter = Limiter(
        key_func=get_remote_address,
        app=app,
        default_limits=["200 per hour"],
        storage_uri="memory://"
    )
except ImportError:
    class MockLimiter:
        def __init__(self, *args, **kwargs):
            pass
        def limit(self, *args, **kwargs):
            def decorator(f):
                return f
            return decorator
        def exempt(self, f):
            return f
    limiter = MockLimiter()

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

app.register_blueprint(general_bp)
app.register_blueprint(signals_bp)
app.register_blueprint(interactions_bp)
app.register_blueprint(boxed_warnings_bp)
app.register_blueprint(ml_bp)
app.register_blueprint(dashboard_bp)
app.register_blueprint(analytics_bp)
app.register_blueprint(temporal_bp)
app.register_blueprint(demographics_bp)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
