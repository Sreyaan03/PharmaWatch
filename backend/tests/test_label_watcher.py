"""
Label watcher: the sentence diff, and that a label change becomes exactly one event while an
unchanged or first-seen label publishes nothing.
"""

import os
import sys

import pytest

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from ingest import label_watcher as lw  # noqa: E402

# Two versions of a saved label. v2 adds a boxed-warning sentence and rewords adverse reactions.
LABEL_V1 = {"found": True, "set_id": "set-1", "version": "3", "effective_time": "20240101",
            "generic_name": "metformin",
            "sections": {"boxed_warning": "WARNING: LACTIC ACIDOSIS. Metformin can cause lactic acidosis.",
                         "adverse_reactions": "The most common reactions are diarrhea and nausea."}}
LABEL_V2 = {"found": True, "set_id": "set-1", "version": "4", "effective_time": "20250601",
            "generic_name": "metformin",
            "sections": {"boxed_warning": "WARNING: LACTIC ACIDOSIS. Metformin can cause lactic acidosis. "
                                          "Discontinue if acidosis is suspected.",
                         "adverse_reactions": "The most common reactions are diarrhea and nausea."}}


def test_split_sentences_keeps_only_meaningful_sentences():
    out = lw.split_sentences("WARNING: LACTIC ACIDOSIS. Short. Metformin can cause lactic acidosis.")
    assert "WARNING: LACTIC ACIDOSIS" in out and "Metformin can cause lactic acidosis" in out
    assert "Short" not in out                      # too short to be a sentence


def test_diff_lists_only_the_added_sentences():
    changed = lw.diff_sections(LABEL_V1["sections"], LABEL_V2["sections"])
    assert set(changed) == {"boxed_warning"}
    assert changed["boxed_warning"] == ["Discontinue if acidosis is suspected"]


def test_a_whole_new_section_counts_as_added():
    changed = lw.diff_sections({"boxed_warning": "A."}, {"boxed_warning": "A.", "warnings": "New risk identified here."})
    assert set(changed) == {"warnings"} and changed["warnings"] == ["New risk identified here"]


def test_reordered_or_reworded_whitespace_is_not_a_change():
    changed = lw.diff_sections({"warnings": "Risk of   bleeding.  Monitor closely."},
                               {"warnings": "Risk of bleeding.\nMonitor closely."})
    assert changed == {}


@pytest.fixture
def snapshot_store(monkeypatch):
    rows = {}

    def _stored(rxcui):
        return rows.get(rxcui)

    def _store(rxcui, label):
        import json
        rows[rxcui] = {"set_id": label.get("set_id"), "version": label.get("version"),
                       "effective_time": label.get("effective_time"),
                       "sections": label.get("sections", {}),
                       "sections_json": json.dumps(label.get("sections", {}))}
    monkeypatch.setattr(lw, "_stored", _stored)
    monkeypatch.setattr(lw, "_store", _store)
    return rows


def test_first_fetch_only_stores_a_baseline(snapshot_store):
    assert lw.check_drug("6809", "metformin", fetch=lambda n: LABEL_V1) is None
    assert "6809" in snapshot_store                # stored, nothing to publish


def test_an_unchanged_label_publishes_nothing(snapshot_store):
    lw.check_drug("6809", "metformin", fetch=lambda n: LABEL_V1)
    assert lw.check_drug("6809", "metformin", fetch=lambda n: LABEL_V1) is None


def test_a_changed_label_publishes_the_added_sentence(snapshot_store):
    lw.check_drug("6809", "metformin", fetch=lambda n: LABEL_V1)
    change = lw.check_drug("6809", "metformin", fetch=lambda n: LABEL_V2)
    assert change is not None
    assert change["rxcui"] == "6809" and change["effective_time"] == "20250601"
    assert change["previous_effective_time"] == "20240101"
    assert change["changed_sections"] == {"boxed_warning": ["Discontinue if acidosis is suspected"]}


def test_a_version_bump_with_no_new_sentences_is_not_a_change(snapshot_store):
    lw.check_drug("6809", "metformin", fetch=lambda n: LABEL_V1)
    bumped = {**LABEL_V1, "version": "5", "effective_time": "20250101"}   # same text, new date
    assert lw.check_drug("6809", "metformin", fetch=lambda n: bumped) is None


def test_a_drug_with_no_label_is_skipped(snapshot_store):
    assert lw.check_drug("999", "notadrug", fetch=lambda n: {"found": False, "error": "no_label"}) is None
    assert "999" not in snapshot_store


def test_poll_publishes_one_event_per_changed_drug(snapshot_store, monkeypatch):
    monkeypatch.setattr(lw, "watched_drugs", lambda limit=None: [("6809", "metformin"), ("11289", "warfarin")])
    labels = {"metformin": LABEL_V1, "warfarin": LABEL_V1}
    monkeypatch.setattr(lw, "fetch_label_sections", lambda n: labels[n])
    # patch check_drug's default fetch by patching the module fetch it closes over
    import ingest.label_watcher as mod
    monkeypatch.setattr(mod, "fetch_label_sections", lambda n: labels[n])
    published = []
    # first run: baselines only
    assert lw.poll_labels(None, publish=lambda p, c: published.append(c)) == 0
    # now warfarin's label changes
    labels["warfarin"] = {**LABEL_V2, "generic_name": "warfarin"}
    assert lw.poll_labels(None, publish=lambda p, c: published.append(c)) == 1
    assert len(published) == 1 and published[0]["generic_name"] == "warfarin"
