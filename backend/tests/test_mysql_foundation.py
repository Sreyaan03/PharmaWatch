"""
test_mysql_foundation.py — Day 1 Verification Unit Tests for PharmaWatch
Validates schema definition, DDL statements, clean_row casting, and configuration helpers.
"""

import os
import sys
import pytest
from decimal import Decimal

# Ensure backend is on sys.path
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from db.mysql import clean_row, get_db_config
from db.migrate import split_sql_statements

EXPECTED_TABLES = [
    "drug_events",
    "drug_events_agg",
    "reddit_posts",
    "boxed_warning_violations",
    "signals_cache",
    "temporal_cache",
    "demographic_cache",
    "interaction_predictions",
    "fda_cache",
    "kafka_pubmed",
    "kafka_trials",
    "kafka_alerts",
    "kafka_pipeline_stats",
]

def test_schema_sql_contains_all_13_tables():
    schema_path = os.path.join(BASE_DIR, "db", "schema.sql")
    assert os.path.exists(schema_path), f"schema.sql not found at {schema_path}"
    with open(schema_path, "r", encoding="utf-8") as f:
        content = f.read()
    
    for table in EXPECTED_TABLES:
        assert f"CREATE TABLE IF NOT EXISTS {table}" in content, f"Table {table} missing from schema.sql"

def test_split_sql_statements():
    schema_path = os.path.join(BASE_DIR, "db", "schema.sql")
    with open(schema_path, "r", encoding="utf-8") as f:
        content = f.read()
    
    statements = split_sql_statements(content)
    assert len(statements) == 13, f"Expected 13 CREATE TABLE statements, got {len(statements)}"
    for stmt in statements:
        assert stmt.strip().upper().startswith("CREATE TABLE"), f"Statement does not start with CREATE TABLE: {stmt[:40]}"

def test_clean_row_decimal_conversion():
    raw_data = {
        "event": "Headache",
        "count": Decimal("42.0"),
        "ratio": Decimal("3.14159"),
        "raw_bytes": b"clean_string",
        "nested": {
            "sub_total": Decimal("100"),
            "items": [Decimal("1.5"), Decimal("2.0")]
        }
    }
    cleaned = clean_row(raw_data)
    
    assert isinstance(cleaned["count"], int)
    assert cleaned["count"] == 42
    assert isinstance(cleaned["ratio"], float)
    assert abs(cleaned["ratio"] - 3.14159) < 1e-5
    assert cleaned["raw_bytes"] == "clean_string"
    assert isinstance(cleaned["nested"]["sub_total"], int)
    assert cleaned["nested"]["sub_total"] == 100
    assert isinstance(cleaned["nested"]["items"][0], float)
    assert isinstance(cleaned["nested"]["items"][1], int)

def test_clean_row_none_handling():
    assert clean_row(None) is None
    assert clean_row([]) == []
    assert clean_row({}) == {}

def test_get_db_config_defaults(monkeypatch):
    monkeypatch.delenv("MYSQL_HOST", raising=False)
    monkeypatch.delenv("MYSQL_PORT", raising=False)
    monkeypatch.delenv("MYSQL_USER", raising=False)
    monkeypatch.delenv("MYSQL_PASSWORD", raising=False)
    monkeypatch.delenv("MYSQL_DATABASE", raising=False)
    
    cfg = get_db_config()
    assert cfg["host"] == "localhost"
    assert cfg["port"] == 3306
    assert cfg["user"] == "root"
    assert cfg["password"] == ""
    assert cfg["database"] == "pharmawatch"

def test_dockerignore_at_repo_root():
    root_dir = os.path.abspath(os.path.join(BASE_DIR, ".."))
    dockerignore_path = os.path.join(root_dir, ".dockerignore")
    assert os.path.exists(dockerignore_path), ".dockerignore missing from repo root"
    
    with open(dockerignore_path, "r", encoding="utf-8") as f:
        content = f.read()
    
    assert ".env" in content
    assert "*.db" in content
    assert "venv/" in content

def test_seed_clean_legacy_data_handles_missing_file():
    from db.migrate import seed_clean_legacy_data
    # Should not throw exception when sqlite file does not exist
    seed_clean_legacy_data(None, "non_existent_file.db")

