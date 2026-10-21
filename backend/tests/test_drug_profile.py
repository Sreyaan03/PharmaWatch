"""
The drug profile endpoint resolves the drug name exactly once and assembles identity, report count,
top reactions, boxed-warning status, trials, external evidence and surveillance alerts from the
existing services. It computes no statistics itself; a store being down degrades one section, not
the whole profile.
"""

import os
import sys

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from app import app  # noqa: E402


def _stub_everything(monkeypatch, resolve_counter):
    from faers import analytics
    from services import boxed_warnings, evidence, signals

    def fake_resolve(name):
        resolve_counter.append(name)
        return {"ready": True, "ok": True, "rxcuis": ["6809"], "unresolved": [], "note": "",
                "identity": {"input": name, "resolved": True, "indexed": True,
                             "ingredients": [{"rxcui": "6809", "name": "METFORMIN"}], "source": "rxnav"}}

    monkeypatch.setattr(signals, "resolve_drug", fake_resolve)
    monkeypatch.setattr(analytics, "get_report_count", lambda d: 1234)
    monkeypatch.setattr(analytics, "get_top_reactions",
                        lambda d, n: [{"reaction": "Nausea", "count": 500}, {"reaction": "Diarrhoea", "count": 300}])
    monkeypatch.setattr(boxed_warnings, "get_boxed_warning", lambda d: ({"has_warning": False, "drug": d}, 200))
    monkeypatch.setattr(boxed_warnings, "get_trials", lambda d: ({"total_studies": 7}, 200))
    monkeypatch.setattr(evidence, "links_for_rxcuis",
                        lambda rxcuis, **k: ({"items": [{"kind": "paper", "id": "1", "title": "x"}],
                                              "counts": {"alert": 0, "paper": 1, "trial": 0}}, 200))
    import db.mysql as mysql
    monkeypatch.setattr(mysql, "query", lambda *a, **k: [])      # no surveillance alerts in this test


def test_profile_resolves_once_and_composes(monkeypatch):
    calls = []
    _stub_everything(monkeypatch, calls)
    client = app.test_client()
    res = client.get("/api/drug/Metformin/profile")
    assert res.status_code == 200
    data = res.get_json()
    assert calls == ["Metformin"]                       # exactly one identity resolution
    assert data["resolved"] is True
    assert data["ingredient"] == {"rxcui": "6809", "name": "METFORMIN"}
    assert data["report_count"] == 1234
    assert [r["reaction"] for r in data["top_reactions"]] == ["Nausea", "Diarrhoea"]
    assert data["boxed_warning"]["has_warning"] is False
    assert data["trials"]["total_studies"] == 7
    assert data["evidence"]["counts"]["paper"] == 1
    assert "reporting pattern" in data["note"] and "not proof" in data["note"]


def test_unresolved_drug_degrades_gracefully(monkeypatch):
    from services import signals
    calls = []

    def fake_resolve(name):
        calls.append(name)
        return {"ready": True, "ok": False, "rxcuis": [], "unresolved": ["drug"],
                "note": "did not resolve",
                "identity": {"input": name, "resolved": False, "ingredients": []}}

    monkeypatch.setattr(signals, "resolve_drug", fake_resolve)
    res = client_get("/api/drug/NotADrug/profile")
    assert res.status_code == 200
    data = res.get_json()
    assert data["resolved"] is False and calls == ["NotADrug"]
    assert "did not resolve" in data["note"]


def test_no_faers_data_is_stated_not_faked(monkeypatch):
    from services import signals
    monkeypatch.setattr(signals, "resolve_drug",
                        lambda name: {"ready": False, "ok": False, "identity": None, "rxcuis": []})
    res = client_get("/api/drug/Metformin/profile")
    data = res.get_json()
    assert data["resolved"] is False and "No FAERS data" in data["note"]


def client_get(path):
    return app.test_client().get(path)
