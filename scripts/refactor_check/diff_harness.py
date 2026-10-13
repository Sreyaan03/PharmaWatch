"""
Differential harness: run every API endpoint against a rich fake network and fake MySQL,
record the full response and every SQL statement, and write one JSON file.
Run it before and after a refactor and diff the two files.

usage (from repo root):  python diff_harness.py OUT.json
"""
import contextlib
import io
import json
import os
import socket
import sys
import urllib.parse

ROOT = os.getcwd()
sys.path.insert(0, os.path.join(ROOT, "backend"))
os.environ["ADMIN_API_KEY"] = "harness-key"

import requests  # noqa: E402

SQL_LOG = []
SCRUB = {"queried_at", "computed_at", "cached_at", "generated_at", "trained_at", "task_id", "server_time"}


# ── fake network ──────────────────────────────────────────────────────────────
def canned(u):
    if "api.fda.gov/drug/event.json" in u:
        if "count=receivedate" in u:
            return {"results": [{"time": f"{y}{m:02d}15", "count": (y - 1990) * 3 + m * 2}
                                for y in range(2003, 2024) for m in (1, 6, 11)]}
        if "count=patient.patientsex" in u:
            return {"results": [{"term": "2", "count": 60}, {"term": "1", "count": 40}, {"term": "0", "count": 5}]}
        if "count=patient.patientonsetage" in u:
            return {"results": [{"term": "15", "count": 3}, {"term": "45", "count": 50}, {"term": "72", "count": 30}]}
        if "count=occurcountry" in u:
            return {"results": [{"term": "US", "count": 90}, {"term": "CA", "count": 20}]}
        if "count=patient.drug.medicinalproduct" in u:
            return {"results": [{"term": "WARFARIN", "count": 500}, {"term": "ASPIRIN", "count": 300},
                                {"term": "METFORMIN", "count": 120}, {"term": "LISINOPRIL", "count": 60}]}
        if "count=patient.reaction" in u:
            return {"results": [{"term": "NAUSEA", "count": 300}, {"term": "HAEMORRHAGE", "count": 200},
                                {"term": "LACTIC ACIDOSIS", "count": 50}, {"term": "DIZZINESS", "count": 30}]}
        return {"meta": {"results": {"total": 100 + (sum(map(ord, u)) % 900)}}}
    if "api.fda.gov/drug/label.json" in u:
        return {"results": [{
            "effective_time": "20150301",
            "boxed_warning": ["WARNING: increased risk of haemorrhage and nausea in elderly patients"],
            "warnings": ["Use caution with aspirin. Bleeding may occur."],
            "drug_interactions": ["Aspirin and warfarin may increase bleeding. Monitor INR."],
            "contraindications": ["Active bleeding"],
            "openfda": {"brand_name": ["BRAND"], "generic_name": ["GENERIC"],
                        "pharm_class_epc": ["Class"], "route": ["ORAL"]}}]}
    if "clinicaltrials.gov" in u:
        return {"studies": [{"protocolSection": {
            "identificationModule": {"nctId": "NCT0001", "briefTitle": "A study"},
            "statusModule": {"overallStatus": "RECRUITING", "startDateStruct": {"date": "2020-01"}},
            "designModule": {"phases": ["PHASE3"], "enrollmentInfo": {"count": 100}},
            "conditionsModule": {"conditions": ["X"]}}}]}
    if "pubchem" in u:
        if "/synonyms/" in u:
            return {"InformationList": {"Information": [{"Synonym": ["Name", "Syn1", "Syn2"]}]}}
        if "/property/" in u:
            return {"PropertyTable": {"Properties": [{"IsomericSMILES": "CC(=O)OC1=CC=CC=C1C(=O)O", "Title": "Aspirin"}]}}
        if "/cids/" in u:
            return {"IdentifierList": {"CID": [2244]}}
    return {}


def fake_request(self, method, url, params=None, **kw):
    full = url
    if params:
        full += ("&" if "?" in url else "?") + urllib.parse.urlencode(params, doseq=True)
    r = requests.Response()
    r.status_code = 200
    r._content = json.dumps(canned(urllib.parse.unquote_plus(full))).encode()
    r.url = full
    r.encoding = "utf-8"
    r.headers["Content-Type"] = "application/json"
    return r


# ── fake MySQL ────────────────────────────────────────────────────────────────
def _norm(sql):
    return " ".join(sql.split())


def canned_one(s):
    s = s.lower()
    if "database() as db" in s:
        return {"db": "pharmawatch", "version": "8.0.0", "server_time": "t"}
    if "sum(case" in s:
        return {f"bin{i}": i for i in range(1, 9)}
    if "count(" in s:
        return {"cnt": 7}
    return None


def canned_all(s):
    s = s.lower()
    if s.startswith("show tables"):
        return [{"Tables_in_pharmawatch": "signals_cache"}]
    if "select distinct drug from signals_cache" in s:
        return [{"drug": "warfarin"}, {"drug": "metformin"}, {"drug": "ibuprofen"}, {"drug": "zzz"}]
    if "group by signal_strength" in s and "date_format" not in s:
        return [{"severity": "critical", "cnt": 2}, {"severity": "high", "cnt": 3}, {"severity": "moderate", "cnt": 5}]
    if "date_format" in s:
        return [{"hour": "10:00", "severity": "high", "cnt": 2}, {"hour": "11:00", "severity": "critical", "cnt": 1}]
    if "from signals_cache" in s and "order by prr" in s:
        return [{"drug": "warfarin", "event": "haemorrhage", "prr": 4.2, "a": 10, "b": 90, "c": 5, "d": 900,
                 "signal_strength": "high", "source": "faers_local"},
                {"drug": "aspirin", "event": "nausea", "prr": 1.1, "a": 3, "b": 30, "c": 20, "d": 800,
                 "signal_strength": "moderate", "source": "openfda"}]
    if "from drug_events group by source" in s:
        return [{"source": "biobert", "c": 3}]
    if "group by topic" in s and "recent" in s:
        return [{"topic": "faers-raw", "recent": 4}]
    if "group by topic" in s:
        return [{"topic": "faers-raw", "total": 10, "last_seen": "x"}]
    if "from kafka_alerts" in s:
        return [{"alert_key": "k", "title": "t", "link": "l", "summary": "s", "published": "p", "ingested_at": "i"}]
    if "from kafka_pubmed" in s:
        return [{"pmid": "1", "title": "t", "journal": "j", "pubdate": "p", "ingested_at": "i"}]
    if "from kafka_trials" in s:
        return [{"nct_id": "N", "drug": "d", "title": "t", "phase": "3", "status": "s", "ingested_at": "i"}]
    if "from interaction_predictions" in s:
        return [{"drug_a": "a", "drug_b": "b", "is_harmful": 1, "confidence": 0.7,
                 "top_side_effect": None, "source": "gnn", "queried_at": "t"}]
    return []


class FakeCursor:
    lastrowid = 0
    rowcount = 1

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.sql = _norm(sql)
        SQL_LOG.append([self.sql, list(params) if params else []])

    def fetchone(self):
        return canned_one(self.sql)

    def fetchall(self):
        return canned_all(self.sql)

    def executemany(self, sql, seq):
        SQL_LOG.append([_norm(sql), "many"])
        return 1


class FakeConn:
    def cursor(self):
        return FakeCursor()

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


@contextlib.contextmanager
def fake_get_db():
    yield FakeConn()


# ── install fakes, then import the app ────────────────────────────────────────
def _refuse(self, *a, **k):
    raise ConnectionRefusedError("blocked by harness")


socket.socket.connect = _refuse
requests.sessions.Session.request = fake_request

from db import mysql  # noqa: E402

mysql.get_db = fake_get_db
mysql.load_dotenv = lambda *a, **k: False

from app import app  # noqa: E402

import faers.analytics as analytics  # noqa: E402

REAL_DUCK = os.path.join(ROOT, "data", "pharmawatch_faers.duckdb")
analytics.DB_PATH = REAL_DUCK  # read-only; exercises the DuckDB code paths

GET = [
    "/", "/api/health", "/api/local-stats", "/api/dashboard/signal-intensity", "/api/dashboard/drug-categories",
    "/api/dashboard/prr-distribution", "/api/pipeline/stats", "/api/pipeline/alerts", "/api/pipeline/pubmed",
    "/api/pipeline/trials", "/api/analytics/prr?drug=Warfarin&event=Nausea",
    "/api/analytics/prr?drug=Metformin&event=Nausea", "/api/analytics/temporal?drug=Metformin",
    "/api/analytics/temporal?drug=Metformin&granularity=quarter", "/api/analytics/top-reactions?drug=Metformin&limit=5",
    "/api/analytics/data-coverage", "/api/signals", "/api/signals?source=openfda", "/api/signals?refresh=true",
    "/api/signals?source=duckdb", "/api/signals/network?drug=Warfarin&event=Bleeding", "/api/signals/network?drug=Warfarin",
    "/api/prr?drug=Warfarin&event=Bleeding", "/api/prr?drug=Warfarin&event=Bleeding&source=openfda",
    "/api/prr?drug=Metformin&event=Nausea&source=duckdb", "/api/prr-trials?drug=Warfarin&event=Bleeding",
    "/api/graph?drug=Metformin", "/api/graph/detailed?drug=Metformin", "/api/graph/twosides?drug=Metformin",
    "/api/graph/twosides", "/api/interactions/resolve?drug=Warfarin", "/api/interactions/resolve?input=CCO",
    "/api/interactions/resolve", "/api/interactions/uncharted?drugs=Warfarin,Aspirin,Metformin&min_reports=5",
    "/api/interactions/uncharted?drugs=Warfarin", "/api/interactions/recent?limit=5",
    "/api/boxed-warning/Warfarin", "/api/boxed-warning-events/Warfarin", "/api/boxed-warning/violations/Warfarin",
    "/api/boxed-warning/violations/all?limit=10", "/api/boxed-warning/timeline/Warfarin",
    "/api/boxed-warning/timeline/Metformin", "/api/boxed-warning/bias-analysis/Warfarin",
    "/api/boxed-warning/bias-analysis/Metformin", "/api/trials/Warfarin", "/api/temporal/velocity?drug=Warfarin&event=Bleeding",
    "/api/temporal/velocity?drug=Metformin&event=Nausea", "/api/temporal/tto?drug=Metformin&event=Nausea",
    "/api/temporal/seasonality?drug=Metformin&event=Nausea", "/api/demographics/sex?drug=Warfarin&event=Bleeding",
    "/api/demographics/sex?drug=Metformin&event=Nausea", "/api/demographics/age?drug=Warfarin&event=Bleeding",
    "/api/demographics/age?drug=Metformin&event=Nausea", "/api/demographics/geo?drug=Warfarin&event=Bleeding",
    "/api/demographics/geo?drug=Metformin&event=Nausea", "/api/demographics/overview?drug=Metformin&event=Nausea",
    "/api/lstm?drug=Metformin", "/api/lstm?drug=Aspirin", "/api/lstm/gap-scan?drug=Metformin",
    "/api/system/bigdata-status", "/api/signals/ner-mine/stream/missing",
]
POST = [
    ("/api/ner", {"json": {"text": "Patient took warfarin and developed severe bleeding."}}),
    ("/api/ner", {"json": {}}),
    ("/api/graph/predict", {"json": {"drug_a": "Warfarin", "drug_b": "Aspirin"}}),
    ("/api/graph/predict", {"json": {"smiles_a": "CCO", "smiles_b": "CC(=O)O"}}),
    ("/api/graph/predict", {"json": {"drug_a": "Warfarin"}}),
    ("/api/interactions/polypharmacy", {"json": {"drugs": ["Warfarin", "Aspirin", "Metformin"]}}),
    ("/api/interactions/polypharmacy", {"json": {"drugs": ["Warfarin"]}}),
    ("/api/lstm/train?drug=Metformin", {"json": {}}),
    ("/api/lstm/train?drug=Metformin&admin_key=wrong", {"json": {}}),
    ("/api/reddit/scrape", {"json": {}}),
    ("/api/reddit/process?admin_key=wrong", {"json": {}}),
    ("/api/system/start-bigdata", {"json": {}}),
    ("/api/system/start-bigdata?admin_key=wrong", {"json": {}}),
]


_TS = __import__("re").compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")


def scrub(o):
    if isinstance(o, str) and _TS.match(o):
        return "<ts>"
    if isinstance(o, dict):
        return {k: ("<scrubbed>" if k in SCRUB else scrub(v)) for k, v in o.items()}
    if isinstance(o, list):
        return [scrub(v) for v in o]
    return o


def record(client_response, label, out, start):
    ctype = client_response.mimetype
    if ctype == "application/json":
        body = scrub(client_response.get_json())
    else:
        text = client_response.get_data(as_text=True)
        body = {"__text__": text if len(text) < 4000 else f"<{len(text)} chars>"}
    if label.startswith("GET /api/dashboard/signal-intensity") and isinstance(body, dict):
        body = {k: (sum(v) if isinstance(v, list) and k != "labels" else ("<labels>" if k == "labels" else v))
                for k, v in body.items()}
    if label.startswith("GET /api/analytics/top-reactions") and isinstance(body, list):
        body = sorted(body, key=lambda r: (-r["count"], r["reaction"]))  # ties are unordered in the source query
    out[label] = {"status": client_response.status_code, "type": ctype, "body": body, "sql": scrub(SQL_LOG[start:])}


def main(path):
    out = {}
    app.config["TESTING"] = True
    client = app.test_client()
    for p in GET:
        start = len(SQL_LOG)
        record(client.get(p), "GET " + p, out, start)
    for p, kw in POST:
        start = len(SQL_LOG)
        record(client.post(p, **kw), "POST " + p + " " + json.dumps(kw.get("json"), sort_keys=True), out, start)
    # streaming NER mining: register, then read the SSE stream
    start = len(SQL_LOG)
    r = client.post("/api/signals/ner-mine", data={"text": "Patient on warfarin developed bleeding and nausea."})
    out["POST /api/signals/ner-mine"] = {"status": r.status_code, "keys": sorted(r.get_json().keys())}
    tid = r.get_json()["task_id"]
    s = client.get(f"/api/signals/ner-mine/stream/{tid}")
    out["GET stream"] = {"status": s.status_code, "type": s.mimetype,
                         "body": s.get_data(as_text=True)[:6000], "sql": scrub(SQL_LOG[start:])}
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(out, f, indent=1, sort_keys=True, default=str)
    from collections import Counter
    print(len(out), "requests;", Counter(v.get("status") for v in out.values()))


if __name__ == "__main__":
    main(sys.argv[1])
