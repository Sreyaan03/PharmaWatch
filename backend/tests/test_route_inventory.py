"""
test_route_inventory.py — Behaviour-preserving safety net for architecture changes.

Records, for every HTTP rule the app registers:
  * the rule and its methods
  * the status code and top-level JSON keys of a call made with the database
    unreachable, DuckDB empty, outbound HTTP returning "{}" and every socket refused.

Those conditions make the snapshot independent of local data, the network and the
machine it runs on. A refactor that moves code without changing behaviour leaves it
untouched; an intended change is recorded with `pytest --update-inventory`.
"""

import json
import os
import socket
import sys

import pytest
import requests

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from app import app  # noqa: E402

INVENTORY_PATH = os.path.join(os.path.dirname(__file__), "fixtures", "route_inventory.json")

# Fixed arguments offered to every call; endpoints ignore the ones they do not read.
QUERY = {
    "drug": "Metformin",
    "event": "Nausea",
    "input": "Metformin",
    "drugs": "Warfarin,Aspirin",
    "granularity": "month",
    "limit": "5",
}
PATH_VALUE = "Metformin"


def _fake_request(self, method, url, *args, **kwargs):
    """Stand-in for requests.Session.request: every outbound call returns 200 and '{}'."""
    resp = requests.Response()
    resp.status_code = 200
    resp._content = b"{}"
    resp.url = url
    resp.encoding = "utf-8"
    resp.headers["Content-Type"] = "application/json"
    return resp


def _refuse(self, *args, **kwargs):
    raise ConnectionRefusedError("sockets are blocked by test_route_inventory")


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    """Database down, DuckDB empty, network answered with '{}', no admin key."""
    from db import mysql

    mysql.reset_pool()  # close pooled connections opened at import time

    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(requests.sessions.Session, "request", _fake_request)
    monkeypatch.delenv("ADMIN_API_KEY", raising=False)
    # Creating a pool re-reads backend/.env; without this the developer's .env
    # (and its ADMIN_API_KEY) would leak into the snapshot and CI would differ.
    monkeypatch.setattr(mysql, "load_dotenv", lambda *a, **k: False)

    # The HBase circuit breaker is process-global; earlier tests may have tripped it.
    for module_name in ("helpers.external_apis", "clients.hbase"):
        module = sys.modules.get(module_name)
        if module is not None and hasattr(module, "_hbase_online_cached"):
            monkeypatch.setattr(module, "_hbase_online_cached", True)
            monkeypatch.setattr(module, "_hbase_last_check", 0.0)

    analytics = sys.modules.get("faers.analytics")
    if analytics is not None:
        monkeypatch.setattr(analytics, "DB_PATH", str(tmp_path / "absent.duckdb"))

    yield

    mysql.reset_pool()


def _rules():
    rules = []
    for rule in app.url_map.iter_rules():
        if rule.endpoint == "static":
            continue
        methods = sorted(m for m in rule.methods if m not in ("HEAD", "OPTIONS"))
        rules.append({"rule": rule.rule, "methods": methods})
    return sorted(rules, key=lambda r: r["rule"])


def _concrete(rule_text):
    out = rule_text
    while "<" in out:
        start = out.index("<")
        end = out.index(">", start)
        out = out[:start] + PATH_VALUE + out[end + 1:]
    return out


def _shape(response):
    payload = response.get_json(silent=True)
    if isinstance(payload, dict):
        keys = sorted(payload.keys())
    elif isinstance(payload, list):
        keys = ["<list>"]
    else:
        keys = [f"<{response.mimetype}>"]
    return {"status": response.status_code, "keys": keys}


def _collect():
    client = app.test_client()
    responses = {}
    for entry in _rules():
        path = _concrete(entry["rule"])
        if not (path == "/" or path.startswith("/api/")):
            continue
        for method in entry["methods"]:
            if method == "GET":
                res = client.get(path, query_string=QUERY)
            elif method == "POST":
                res = client.post(path, query_string=QUERY, json={})
            else:
                continue
            responses[f"{method} {entry['rule']}"] = _shape(res)
    return {"rules": _rules(), "responses": responses}


def test_route_inventory_matches_snapshot(isolated, request):
    app.config["TESTING"] = True
    current = _collect()

    if request.config.getoption("--update-inventory"):
        os.makedirs(os.path.dirname(INVENTORY_PATH), exist_ok=True)
        with open(INVENTORY_PATH, "w", encoding="utf-8", newline="\n") as f:
            json.dump(current, f, indent=2, sort_keys=True)
            f.write("\n")
        pytest.skip("route inventory regenerated")

    assert os.path.exists(INVENTORY_PATH), (
        "Missing snapshot; run `pytest backend/tests/test_route_inventory.py --update-inventory`"
    )
    with open(INVENTORY_PATH, "r", encoding="utf-8") as f:
        expected = json.load(f)

    assert current["rules"] == expected["rules"], "Registered routes changed"

    changed = {
        key: {"expected": expected["responses"].get(key), "actual": current["responses"].get(key)}
        for key in sorted(set(expected["responses"]) | set(current["responses"]))
        if expected["responses"].get(key) != current["responses"].get(key)
    }
    assert not changed, f"Response shape changed for: {json.dumps(changed, indent=2)}"


def test_snapshot_covers_every_api_rule():
    """The snapshot must exercise every API route, so a new route cannot go unrecorded."""
    with open(INVENTORY_PATH, "r", encoding="utf-8") as f:
        expected = json.load(f)
    calls = {
        (r["rule"], m)
        for r in expected["rules"]
        if r["rule"] == "/" or r["rule"].startswith("/api/")
        for m in r["methods"]
        if m in ("GET", "POST")
    }
    assert {f"{m} {rule}" for rule, m in calls} == set(expected["responses"])
