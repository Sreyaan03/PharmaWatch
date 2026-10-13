"""
Boxed-warning features describe reporting; they do not draw causal or behavioural conclusions.

  * a warning year comes only from a sourced record or the caller, never from the label's revision
    date, and a drug without a boxed warning never gets one;
  * a reported event matches the warning text by whole term or reviewed synonym only;
  * the text these endpoints return makes no claim about prescribing, effectiveness or confirmation.
"""

import os
import sys

import pytest

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from app import app  # noqa: E402
from services import boxed_warnings as bw  # noqa: E402
from services.warning_rules import (event_in_warning, load_warning_dates,  # noqa: E402
                                    resolve_warning_year, synonyms_by_term)

WARNING_TEXT = ("WARNING: HEPATOTOXICITY and abdominal pain. Severe liver injury, including "
                "hepatotoxicity, has been reported.")


# ── dates file ────────────────────────────────────────────────────────────────
def test_the_shipped_dates_file_has_no_unsourced_year():
    for name, row in load_warning_dates().items():
        assert row["source_url"].startswith("http"), name


def test_a_row_without_a_source_is_not_a_recorded_year(tmp_path):
    path = tmp_path / "dates.csv"
    path.write_text("ingredient,year,source_url,verified_by\n"
                    "druga,2011,https://example.org/label-history,reviewer\n"
                    "drugb,2005,,reviewer\n"
                    "drugc,,https://example.org/x,reviewer\n", encoding="utf-8")
    table = load_warning_dates(str(path))
    assert list(table) == ["druga"]
    assert resolve_warning_year("DrugA", dates=table)["year"] == 2011
    assert resolve_warning_year("drugb", dates=table)["year"] is None


def test_the_request_parameter_is_used_and_labelled_as_supplied():
    r = resolve_warning_year("anything", override="2012", dates={})
    assert r["year"] == 2012 and r["origin"] == "request"


@pytest.mark.parametrize("bad", ["abc", "1800", "3000", ""])
def test_an_implausible_year_is_ignored(bad):
    assert resolve_warning_year("anything", override=bad, dates={})["year"] is None


def test_no_built_in_year_table_remains():
    assert not hasattr(bw, "HISTORICAL_WARNING_YEARS")
    assert not hasattr(bw, "_get_true_warning_year")


# ── event matching ────────────────────────────────────────────────────────────
def test_pain_in_a_warning_does_not_match_abdominal_pain_upper():
    assert event_in_warning("Pain", "may cause chest pain and headache") is not None   # whole word
    assert event_in_warning("Abdominal pain upper", WARNING_TEXT) is None
    assert event_in_warning("Abdominal pain", WARNING_TEXT) is not None


def test_a_single_shared_word_is_not_a_match():
    text = "risk of liver injury"
    assert event_in_warning("Liver disorder", text) is None
    assert event_in_warning("Injury", text) is not None


def test_a_term_must_not_match_inside_a_longer_word():
    assert event_in_warning("Nausea", "causes nauseating odour") is None


def test_a_reviewed_synonym_matches_and_says_so():
    synonyms = synonyms_by_term({"bleeding": {"pt": "Haemorrhage", "source": "", "note": ""}})
    hit = event_in_warning("Haemorrhage", "risk of serious bleeding events", synonyms)
    assert hit == {"matched": "bleeding", "by": "synonym"}


def test_underscores_and_case_in_openfda_terms_are_normalised():
    assert event_in_warning("HEPATO_TOXICITY".replace("_", " "), "hepato toxicity warned") is not None
    assert event_in_warning("QT PROLONGATION", "Risk of QT prolongation") is not None


# ── endpoints, with the openFDA API replaced ──────────────────────────────────
class _Response:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _FakeOpenFda:
    """Answers the label, total, reaction-count, post-warning and per-year queries."""

    def __init__(self, with_label=True, post_total=12):
        self.with_label = with_label
        self.post_total = post_total
        self.urls = []

    def get(self, url, params=None, timeout=None):
        self.urls.append(url)
        if "clinicaltrials.gov" in url:
            return _Response({"studies": []})
        if "drug/label.json" in url:
            if not self.with_label:
                return _Response({})
            return _Response({"results": [{"boxed_warning": [WARNING_TEXT], "effective_time": "20240115",
                                           "openfda": {"brand_name": ["BRANDA"], "generic_name": ["DRUGA"]}}]})
        if "count=receivedate" in url:
            return _Response({"results": [{"time": f"{y}0615", "count": 100 + 10 * (y - 2000)}
                                           for y in range(2000, 2027)]})
        if "receivedate:[" in url:
            return _Response({"meta": {"results": {"total": self.post_total}}})
        if "count=patient.reaction" in url:
            return _Response({"results": [{"term": "HEPATOTOXICITY", "count": 50},
                                          {"term": "ABDOMINAL PAIN UPPER", "count": 30},
                                          {"term": "NAUSEA", "count": 100}]})
        return _Response({"meta": {"results": {"total": 1000}}})


@pytest.fixture
def client(monkeypatch):
    stored = []
    monkeypatch.setattr(bw, "insert_violation", lambda **k: stored.append(k))
    monkeypatch.setattr(bw, "get_violations", lambda drug: [])
    monkeypatch.setattr(bw, "_synonyms", lambda: {})
    app.config["TESTING"] = True
    c = app.test_client()
    c.stored = stored
    return c


def _use(monkeypatch, **kw):
    fake = _FakeOpenFda(**kw)
    monkeypatch.setattr(bw, "http_requests", fake)
    return fake


def test_a_drug_without_a_boxed_warning_never_receives_a_warning_year(client, monkeypatch):
    _use(monkeypatch, with_label=False)
    for path in ("timeline", "bias-analysis", "post-warning-reports"):
        body = client.get(f"/api/boxed-warning/{path}/drugb?warning_year=2010").get_json()
        assert body["has_boxed_warning"] is False, path
        assert body.get("warning_year") is None, path
        assert "no boxed warning" in body["message"].lower(), path
    assert client.stored == []


def test_the_label_revision_date_is_not_the_warning_year(client, monkeypatch):
    _use(monkeypatch)
    body = client.get("/api/boxed-warning/timeline/druga").get_json()
    assert body["label_revision_date"] == "2024-01-15"
    assert body["warning_year"] is None and body["warning_index"] is None
    assert body["pre_slope"] is None and body["post_slope"] is None
    assert "no sourced warning year" in body["interpretation"].lower()


def test_without_a_sourced_year_reports_are_not_split(client, monkeypatch):
    fake = _use(monkeypatch)
    body = client.get("/api/boxed-warning/post-warning-reports/druga").get_json()
    assert [e["term"] for e in body["events"]] == ["HEPATOTOXICITY"]       # not "ABDOMINAL PAIN UPPER"
    assert body["events"][0]["post_count"] is None and body["events"][0]["pre_count"] is None
    assert body["warning_year"] is None and body["warning_date"] == "unknown"
    assert not any("receivedate:[" in u for u in fake.urls)
    assert client.stored == []                                             # nothing persisted


def test_with_a_supplied_year_the_split_is_made_and_labelled(client, monkeypatch):
    _use(monkeypatch, post_total=12)
    body = client.get("/api/boxed-warning/post-warning-reports/druga?warning_year=2010").get_json()
    e = body["events"][0]
    assert (e["total_count"], e["post_count"], e["pre_count"]) == (50, 12, 38)
    assert body["warning_year"] == 2010 and body["warning_year_source"] == "request"
    assert body["label_revision_date"] == "2024-01-15"
    assert len(client.stored) == 1 and client.stored[0]["warning_effective_date"] == "2010-01-01"


def test_old_paths_return_the_same_data(client, monkeypatch):
    _use(monkeypatch)
    new = client.get("/api/boxed-warning/post-warning-reports/druga?warning_year=2010").get_json()
    old = client.get("/api/boxed-warning/violations/druga?warning_year=2010").get_json()
    new.pop("meta"), old.pop("meta")
    assert new == old
    assert old["violations"] == old["events"]


def test_timeline_compares_slopes_only_for_a_given_year(client, monkeypatch):
    _use(monkeypatch)
    body = client.get("/api/boxed-warning/timeline/druga?warning_year=2012").get_json()
    assert body["warning_year"] == 2012 and body["warning_index"] == body["labels"].index("2012")
    assert body["pre_slope"] is not None and body["post_slope"] is not None
    assert str(2026) in body["labels"]                                      # current year is kept


# ── wording ───────────────────────────────────────────────────────────────────
BANNED = ("prescrib", "despite", "effective", "confirmed", "corroborat", "violat", "reduced access",
          "warning worked", "not working", "dampened")


def _strings(node):
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for v in node.values():
            yield from _strings(v)
    elif isinstance(node, list):
        for v in node:
            yield from _strings(v)


def _check_wording(body, path):
    for text in _strings(body):
        low = text.lower()
        if text == WARNING_TEXT:                    # the label's own words are quoted, not ours
            continue
        for word in BANNED:
            assert word not in low, f"{path}: {word!r} in {text[:120]!r}"


@pytest.mark.parametrize("query", ["", "?warning_year=2012"])
def test_wording_endpoints_describe_reporting_only(client, monkeypatch, query):
    """Text returned by the boxed-warning endpoints describes reporting only."""
    _use(monkeypatch)
    monkeypatch.setattr("services.signals.compute_disproportionality_helper",
                        lambda d, e: {"a": 10, "b": 90, "c": 20, "d": 880, "prr": 5.0,
                                      "signal_strength": "strong_signal"})
    for path in ("timeline/druga", "bias-analysis/druga", "post-warning-reports/druga"):
        body = client.get(f"/api/boxed-warning/{path}{query}").get_json()
        _check_wording(body, path)
    body = client.get("/api/prr-trials?drug=druga&event=Nausea").get_json()
    _check_wording(body, "prr-trials")
    assert body["trial_context"] == "SIGNAL_ONLY"


def test_wording_the_literature_card_text_in_the_frontend_is_descriptive():
    """The strings the page itself adds around these numbers."""
    src = os.path.join(BASE_DIR, "..", "src")
    for name in ("app.js", "guide.js", "index.html"):
        text = open(os.path.join(src, name), encoding="utf-8").read().lower()
        for phrase in ("prescriptions continued", "warning dampened reporting",
                       "warning reduced reporting", "strong corroboration", "partial corroboration",
                       "contrary to boxed warnings", "prescriber violations"):
            assert phrase not in text, f"{name}: {phrase!r}"


def test_a_registered_trial_is_not_called_confirmation(client, monkeypatch):
    _use(monkeypatch)
    monkeypatch.setattr("services.signals.compute_disproportionality_helper",
                        lambda d, e: {"a": 0, "b": 0, "c": 0, "d": 0, "prr": 0.0,
                                      "signal_strength": "none", "unresolved": ["drug"]})
    body = client.get("/api/prr-trials?drug=nosuch&event=Nausea").get_json()
    assert body["trial_context"] == "NOT_AVAILABLE" and body["is_signal"] is False
