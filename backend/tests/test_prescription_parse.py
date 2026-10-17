"""
Prescription text -> structured medication lines (dictionary + rules; no model, no network).

The parser reads what is written and never fills in what is missing.
"""

import os
import sys

import pytest

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from services.prescription.lexicon import DrugLexicon  # noqa: E402
from services.prescription.parse import (parse_duration, parse_frequency, parse_medications,  # noqa: E402
                                         split_chunks)

LEX = DrugLexicon({
    "metformin": "metformin", "metformin hydrochloride": "metformin", "glucophage": "metformin",
    "warfarin": "warfarin", "coumadin": "warfarin", "amlodipine": "amlodipine", "atorvastatin": "atorvastatin",
    "pantoprazole": "pantoprazole", "paracetamol": "acetaminophen", "ibuprofen": "ibuprofen",
    "clopidogrel": "clopidogrel", "tablet": "tablet", "sodium": "sodium", "water": "water",
})


def one(text):
    lines = parse_medications(text, LEX)
    assert len(lines) == 1, [l.drug_text for l in lines]
    return lines[0]


# ── dosing fields ─────────────────────────────────────────────────────────────
def test_a_standard_line_is_read_field_by_field():
    m = one("Tab. Metformin 500 mg PO BD x 30 days")
    assert (m.drug_text, m.ingredient, m.match) == ("Metformin", "metformin", "exact")
    assert m.strength == "500 mg" and m.dose_form == "tablet" and m.route == "oral"
    assert m.frequency == "twice daily" and m.duration == "30 days" and not m.as_needed


def test_brand_names_map_to_their_ingredient():
    m = one("Coumadin 5 mg once daily")
    assert m.drug_text == "Coumadin" and m.ingredient == "warfarin" and m.frequency == "once daily"


@pytest.mark.parametrize("text,expected", [
    ("OD", "once daily"), ("twice a day", "twice daily"), ("TDS", "three times daily"),
    ("QID", "four times daily"), ("at night", "at bedtime"), ("HS", "at bedtime"),
    ("weekly", "once weekly"), ("q8h", "every 8 hours"), ("every 6 hours", "every 6 hours"),
])
def test_frequency_abbreviations(text, expected):
    assert parse_frequency(f"drug 10 mg {text}")[0] == expected


@pytest.mark.parametrize("schedule,expected", [("1-0-1", "twice daily"), ("1-1-1", "three times daily"),
                                                ("0-0-1", "once daily"), ("1-0-0", "once daily")])
def test_morning_noon_night_schedules(schedule, expected):
    freq, _, note = parse_frequency(f"Amlodipine 5 mg {schedule}")
    assert freq == expected and schedule in note


def test_as_needed_is_flagged_and_gives_no_invented_frequency():
    m = one("Ibuprofen 400 mg PRN")
    assert m.as_needed is True and m.frequency is None


def test_missing_fields_stay_empty_not_guessed():
    m = one("Warfarin")
    assert m.strength is None and m.frequency is None and m.route is None and m.duration is None
    assert m.confidence < 0.8


def test_duration_forms():
    assert parse_duration("for 2 weeks") == "2 weeks" and parse_duration("x 5 d") == "5 days"
    assert parse_duration("continue for 3 months") == "3 months" and parse_duration("take daily") is None


# ── several medications, layout ──────────────────────────────────────────────
def test_a_numbered_list_gives_one_line_each():
    text = "1. Tab Metformin 500 mg BD\n2. Tab Amlodipine 5 mg OD\n3. Cap Pantoprazole 40 mg OD before food"
    lines = parse_medications(text, LEX)
    assert [l.ingredient for l in lines] == ["metformin", "amlodipine", "pantoprazole"]
    assert [l.frequency for l in lines] == ["twice daily", "once daily", "once daily"]


def test_two_drugs_on_one_ocr_line_are_separated():
    lines = parse_medications("Metformin 500 mg BD Atorvastatin 20 mg HS", LEX)
    assert [(l.ingredient, l.strength, l.frequency) for l in lines] == [
        ("metformin", "500 mg", "twice daily"), ("atorvastatin", "20 mg", "at bedtime")]


def test_each_drug_gets_only_its_own_dosing():
    lines = parse_medications("Metformin 500 mg BD Warfarin 5 mg OD", LEX)
    assert lines[0].strength == "500 mg" and lines[1].strength == "5 mg"


def test_split_chunks_handles_semicolons_and_markers():
    assert split_chunks("a drug 1; b drug 2\n3) c drug") == ["a drug 1", "b drug 2", "c drug"]


# ── context: stopped, allergy ────────────────────────────────────────────────
def test_stopped_and_allergy_lines_are_not_treated_as_active_medications():
    assert one("STOP warfarin 5 mg").status == "stopped"
    assert one("Hold metformin until review").status == "stopped"
    allergy = one("Allergic to ibuprofen")
    assert allergy.status == "allergy" and "allergy" in allergy.notes


# ── names ────────────────────────────────────────────────────────────────────
def test_an_ocr_slip_is_a_flagged_fuzzy_match_with_lower_confidence():
    clean, slip = one("Atorvastatin 20 mg OD"), one("Atorvastaiin 20 mg OD")
    assert slip.match == "fuzzy" and slip.ingredient == "atorvastatin" and "close to" in slip.notes
    assert slip.confidence < clean.confidence


def test_an_unknown_dosed_name_is_kept_as_written_and_flagged():
    m = one("Tab. Glycomet 500 mg BD")
    assert m.drug_text == "Glycomet" and m.match is None and m.ingredient is None
    assert "not found in the drug dictionary" in m.notes and m.confidence < 0.6


def test_ordinary_words_are_not_drugs():
    assert parse_medications("Take with water after food. Follow up in two weeks.", LEX) == []
    assert parse_medications("Sodium 140, water intake 2 litres", LEX) == []


def test_short_words_are_never_fuzzy_matched():
    assert parse_medications("Rx metfor 500", LEX) == []        # too short to trust a fuzzy match


# ── confidence ───────────────────────────────────────────────────────────────
def test_confidence_rises_with_a_complete_line_and_a_clean_read():
    full = one("Tab. Metformin 500 mg PO BD")
    bare = one("Metformin")
    assert full.confidence > bare.confidence
    low_ocr = parse_medications("Tab. Metformin 500 mg PO BD", LEX,
                                line_confidences={"Tab. Metformin 500 mg PO BD": 0.3})[0]
    assert low_ocr.confidence < full.confidence
