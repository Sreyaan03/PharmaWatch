"""
The label-text check: exact and variant matching, the reported section and sentence, synonyms,
the never-"unlabelled" wording, and the no-label case.
"""

import os
import sys

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from services import label_check as lc  # noqa: E402

SECTIONS = {
    "boxed_warning": "WARNING: LACTIC ACIDOSIS. Metformin can cause lactic acidosis.",
    "warnings_and_cautions": "Vitamin B12 Deficiency may occur. Peripheral neuropathy has been reported.",
    "adverse_reactions": "The most common reactions are diarrhea and nausea. Cataracts were seen.",
}


def test_exact_term_is_found_with_section_and_sentence():
    r = lc.check_sections(SECTIONS, "Lactic acidosis")
    assert r["status"] == "in_label_text" and r["section"] == "boxed_warning"
    assert "lactic acidosis" in r["sentence"].lower()


def test_word_order_variant_is_found():
    r = lc.check_sections(SECTIONS, "Neuropathy peripheral")       # label says "peripheral neuropathy"
    assert r["status"] == "in_label_text" and r["matched"] == "peripheral neuropathy"


def test_spelling_variant_is_found():
    r = lc.check_sections(SECTIONS, "Diarrhoea")                   # label says "diarrhea"
    assert r["status"] == "in_label_text" and r["section"] == "adverse_reactions"


def test_plural_in_label_matches_singular_term():
    r = lc.check_sections(SECTIONS, "Cataract")                    # label says "Cataracts"
    assert r["status"] == "in_label_text"


def test_synonym_from_the_reviewed_file_is_found():
    r = lc.check_sections({"warnings": "Serious bleeding may occur."}, "Haemorrhage")
    assert r["status"] == "in_label_text" and r["matched"] in ("bleeding", "haemorrhage", "hemorrhage")


def test_an_absent_effect_is_not_found_and_lists_what_was_searched():
    r = lc.check_sections(SECTIONS, "Rhabdomyolysis")
    assert r["status"] == "not_found_in_label_text" and "rhabdomyolysis" in r["searched"]


def test_the_most_specific_section_is_reported_first():
    sections = {"adverse_reactions": "Lactic acidosis reported.", "boxed_warning": "Lactic acidosis risk."}
    assert lc.check_sections(sections, "Lactic acidosis")["section"] == "boxed_warning"


def test_wording_is_never_unlabelled_or_unknown():
    r = lc.check_sections(SECTIONS, "Rhabdomyolysis")
    assert "unlabelled" not in str(r).lower() and r["status"] == "not_found_in_label_text"


def test_no_label_stored_is_reported_as_no_label(monkeypatch):
    monkeypatch.setattr(lc, "stored_sections", lambda rxcui: None)
    assert lc.find("999", "Nausea")["status"] == "no_label"


def test_find_uses_the_stored_label(monkeypatch):
    monkeypatch.setattr(lc, "stored_sections", lambda rxcui: {"generic_name": "metformin",
                        "effective_time": "20250101", "sections": SECTIONS})
    r = lc.find("6809", "Lactic acidosis")
    assert r["status"] == "in_label_text" and r["generic_name"] == "metformin"
    assert r["label_effective_time"] == "20250101" and r["rxcui"] == "6809"


def test_a_substring_that_is_not_a_whole_word_does_not_match():
    # "ia" inside "acidosis" must not match a term "ia"; whole-word only
    r = lc.check_sections({"warnings": "Lactic acidosis."}, "osis")
    assert r["status"] == "not_found_in_label_text"
