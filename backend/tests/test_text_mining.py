"""
Text mining forms a drug-event mention only within one sentence and drops events under a negation
cue. Pure logic over NER entities — no model, no database.
"""

import os
import sys

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from services import text_mining  # noqa: E402


def _entities(text, items):
    """Build NER-style entities by locating each (token, label) in the text."""
    out = []
    for token, label in items:
        start = text.index(token)
        out.append({"token": token, "label": label, "score": 0.9,
                    "start": start, "end": start + len(token)})
    return out


def test_negated_event_yields_no_pair():
    text = "No bleeding was observed on warfarin."
    ents = _entities(text, [("bleeding", "Sign_symptom"), ("warfarin", "Medication")])
    assert text_mining.pair_mentions(text, ents) == []


def test_same_sentence_pair_is_formed():
    text = "The patient started warfarin and developed severe bleeding."
    ents = _entities(text, [("warfarin", "Medication"), ("bleeding", "Sign_symptom")])
    pairs = text_mining.pair_mentions(text, ents)
    assert len(pairs) == 1
    assert pairs[0]["drug"] == "warfarin" and pairs[0]["event"] == "bleeding"


def test_cross_sentence_pair_is_not_formed():
    text = "Metformin was well tolerated. The patient later developed a rash."
    ents = _entities(text, [("Metformin", "Medication"), ("rash", "Sign_symptom")])
    assert text_mining.pair_mentions(text, ents) == []


def test_denies_cue_drops_event():
    text = "On atorvastatin; the patient denies myalgia."
    ents = _entities(text, [("atorvastatin", "Medication"), ("myalgia", "Sign_symptom")])
    assert text_mining.pair_mentions(text, ents) == []


def test_multiple_events_one_sentence():
    text = "Ibuprofen caused nausea and dizziness in this patient."
    ents = _entities(text, [("Ibuprofen", "Medication"), ("nausea", "Sign_symptom"),
                            ("dizziness", "Sign_symptom")])
    pairs = text_mining.pair_mentions(text, ents)
    events = sorted(p["event"] for p in pairs)
    assert events == ["dizziness", "nausea"]


def test_negation_only_affects_its_own_sentence():
    text = "No fever on amoxicillin. The patient then developed a rash on ibuprofen."
    ents = _entities(text, [("fever", "Sign_symptom"), ("amoxicillin", "Medication"),
                            ("rash", "Sign_symptom"), ("ibuprofen", "Medication")])
    pairs = text_mining.pair_mentions(text, ents)
    # 'fever' is negated in sentence 1; 'rash' in sentence 2 is kept
    assert [p["event"] for p in pairs] == ["rash"]
