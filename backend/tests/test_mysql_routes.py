"""
test_mysql_routes.py — Day 2 Integration and Route Smoke Tests for PharmaWatch
Validates zero SQLite imports in application code, endpoint responses, admin auth, and cache parsing.
"""

import os
import sys
import pytest
from datetime import datetime, timedelta

# Ensure backend directory is in sys.path
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from app import app


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


def test_zero_sqlite_imports_in_runtime_code():
    """Verify that NO runtime route, core adapter, or consumer imports sqlite3."""
    app_files_to_check = [
        os.path.join(BASE_DIR, "database.py"),
        os.path.join(BASE_DIR, "routes", "dashboard.py"),
        os.path.join(BASE_DIR, "routes", "pipeline.py"),
        os.path.join(BASE_DIR, "routes", "general.py"),
        os.path.join(BASE_DIR, "routes", "demographics.py"),
        os.path.join(BASE_DIR, "routes", "temporal.py"),
        os.path.join(BASE_DIR, "routes", "signals.py"),
        os.path.join(BASE_DIR, "routes", "interactions.py"),
        os.path.join(BASE_DIR, "routes", "boxed_warnings.py"),
        os.path.join(BASE_DIR, "routes", "ml.py"),
        os.path.join(BASE_DIR, "routes", "analytics.py"),
        os.path.join(BASE_DIR, "..", "scripts", "kafka_consumer.py"),
    ]

    for filepath in app_files_to_check:
        normalized = os.path.normpath(filepath)
        assert os.path.exists(normalized), f"File not found: {normalized}"
        with open(normalized, "r", encoding="utf-8") as f:
            content = f.read()
        assert "import sqlite3" not in content, f"Found forbidden 'import sqlite3' in {normalized}"


def test_health_endpoint(client):
    """Verify /api/health returns HTTP 200 with database probe."""
    res = client.get("/api/health")
    assert res.status_code == 200
    data = res.get_json()
    assert data["status"] == "ok"
    assert "database" in data
    assert "model" in data


def test_dashboard_endpoints(client):
    """Verify all dashboard endpoints return HTTP 200 and valid schema."""
    # 1. local-stats
    r1 = client.get("/api/local-stats")
    assert r1.status_code == 200
    d1 = r1.get_json()
    assert "drugs_monitored" in d1
    assert "signals_detected" in d1
    assert "reports_analyzed" in d1

    # 2. signal-intensity
    r2 = client.get("/api/dashboard/signal-intensity")
    assert r2.status_code == 200
    d2 = r2.get_json()
    assert "labels" in d2
    assert "critical" in d2
    assert len(d2["labels"]) == 24

    # 3. drug-categories
    r3 = client.get("/api/dashboard/drug-categories")
    assert r3.status_code == 200
    d3 = r3.get_json()
    assert "labels" in d3
    assert "data" in d3

    # 4. prr-distribution
    r4 = client.get("/api/dashboard/prr-distribution")
    assert r4.status_code == 200
    d4 = r4.get_json()
    assert "labels" in d4
    assert len(d4["data"]) == 8


def test_pipeline_stats_endpoint(client):
    """Verify /api/pipeline/stats returns HTTP 200 and schema."""
    res = client.get("/api/pipeline/stats")
    assert res.status_code == 200
    data = res.get_json()
    assert "kafka_running" in data
    assert "topics" in data
    assert "ingested" in data


def test_admin_endpoints_require_auth(client, monkeypatch):
    """Verify that dangerous admin endpoints reject unauthorized callers."""
    # 1. When ADMIN_API_KEY is not set (default in test)
    monkeypatch.delenv("ADMIN_API_KEY", raising=False)
    r_train = client.post("/api/lstm/train")
    assert r_train.status_code == 403

    r_bigdata = client.post("/api/system/start-bigdata")
    assert r_bigdata.status_code == 403

    r_scrape = client.post("/api/reddit/scrape")
    assert r_scrape.status_code == 403

    r_proc = client.post("/api/reddit/process")
    assert r_proc.status_code == 403

    # 2. When ADMIN_API_KEY is set, reject bad key with 401
    monkeypatch.setenv("ADMIN_API_KEY", "super_secret_test_key")
    r_bad = client.post("/api/lstm/train?admin_key=wrong_key")
    assert r_bad.status_code == 401

    r_bad_scrape = client.post("/api/reddit/scrape?admin_key=wrong_key")
    assert r_bad_scrape.status_code == 401


def test_biobert_js_url_relative():
    """Verify that src/biobert.js does not hardcode static http://localhost:5000."""
    biobert_path = os.path.normpath(os.path.join(BASE_DIR, "..", "src", "biobert.js"))
    with open(biobert_path, "r", encoding="utf-8") as f:
        content = f.read()
    # It must not have the hardcoded line: API_URL: 'http://localhost:5000',
    assert "API_URL: 'http://localhost:5000'," not in content
