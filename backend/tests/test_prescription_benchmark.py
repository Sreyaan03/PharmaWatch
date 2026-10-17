"""Scoring of the prescription benchmark: it must count missed, wrong and invented lines honestly."""

import os
import sys

import pytest

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from validation import prescription_benchmark as B  # noqa: E402


def gt(drug, ing, **kw):
    return {"drug_text": drug, "ingredient": ing, "strength": kw.get("strength"), "frequency": kw.get("frequency"),
            "duration": kw.get("duration"), "route": kw.get("route"), "status": kw.get("status", "active")}


def ex(drug, ing, **kw):
    return {**gt(drug, ing, **kw), "match": kw.get("match", "exact"), "confidence": kw.get("confidence", 0.9)}


def test_a_perfect_extraction_scores_one():
    g = [gt("Warfarin", "warfarin", strength="5 mg", frequency="once daily")]
    s = B.score_document(g, [ex("Warfarin", "warfarin", strength="5mg", frequency="once daily")])
    o = B.summarise(B.merge([s]))
    assert o["line_recall"] == 1.0 and o["line_precision"] == 1.0 and o["strength_accuracy"] == 1.0
    assert o["lines_invented"] == 0 and o["lines_missed"] == 0


def test_missed_and_invented_lines_are_counted_separately():
    g = [gt("Warfarin", "warfarin"), gt("Aspirin", "aspirin")]
    s = B.score_document(g, [ex("Warfarin", "warfarin"), ex("Phantom", "phantom")])
    o = B.summarise(B.merge([s]))
    assert (o["lines_missed"], o["lines_invented"]) == (1, 1) and o["line_recall"] == 0.5 and o["line_precision"] == 0.5
    problems = {(d["line"], d["problem"]) for d in s["details"]}
    assert ("Aspirin", "missed") in problems and ("Phantom", "invented") in problems


def test_a_wrong_value_and_an_empty_value_are_different_failures():
    g = [gt("Metformin", "metformin", frequency="twice daily", duration="5 days")]
    s = B.score_document(g, [ex("Metformin", "metformin", frequency="once daily", duration=None)])
    f = s["fields"]
    assert f["frequency"] == {"expected": 1, "correct": 0, "wrong": 1, "empty": 0}
    assert f["duration"] == {"expected": 1, "correct": 0, "wrong": 0, "empty": 1}


def test_only_fields_the_ground_truth_has_are_scored():
    s = B.score_document([gt("Aspirin", "aspirin")], [ex("Aspirin", "aspirin", strength="75 mg")])
    assert s["fields"]["strength"]["expected"] == 0           # printed without a strength: not scored, not penalised


def test_lines_are_aligned_by_drug_text_then_by_ingredient():
    g = [gt("Coumadin", "warfarin")]
    s = B.score_document(g, [ex("Coumadln", "warfarin")])      # OCR slip in the name, ingredient still found
    assert s["matched"] == 1 and s["invented"] == 0


def test_a_wrong_ingredient_counts_and_flagging_is_measured():
    g = [gt("Glycomet", "metformin"), gt("Ecosprin", "aspirin")]
    s = B.score_document(g, [ex("Glycomet", None, match=None, confidence=0.4), ex("Ecosprin", "aspirin")])
    assert s["unrecognised"] == {"wrong_ingredient": 1, "flagged": 1}
    s2 = B.score_document([gt("Glycomet", "metformin")], [ex("Glycomet", "ibuprofen", match="exact", confidence=0.95)])
    assert s2["unrecognised"] == {"wrong_ingredient": 1, "flagged": 0}      # confidently wrong: reported as such


def test_status_is_scored_for_stopped_and_allergy_lines():
    g = [gt("Warfarin", "warfarin", status="stopped")]
    assert B.score_document(g, [ex("Warfarin", "warfarin", status="active")])["status"] == {"expected": 1, "correct": 0}


def test_review_findings_are_matched_by_category_and_ingredients():
    findings = [{"category": "documented_interaction", "title": "Warfarin + Aspirin", "detail": ""},
                {"category": "boxed_warning", "title": "warfarin has a boxed warning", "detail": ""}]
    assert B.finding_present({"category": "documented_interaction", "ingredients": ["aspirin", "warfarin"]}, findings)
    assert not B.finding_present({"category": "duplicate_therapy", "ingredients": ["warfarin"]}, findings)
    assert not B.finding_present({"category": "documented_interaction", "ingredients": ["warfarin", "digoxin"]}, findings)


def test_the_cache_returns_what_the_reader_returned_and_reads_once(tmp_path):
    class Reader:
        calls = 0

        def readtext(self, data):
            Reader.calls += 1
            return [([[0, 0], [10, 0], [10, 5], [0, 5]], "Warfarin", 0.9)]

    c = B.CachingReader(Reader(), str(tmp_path / "ocr.json"))
    first, second = c.readtext(b"image"), c.readtext(b"image")
    assert first == second and Reader.calls == 1
    again = B.CachingReader(Reader(), str(tmp_path / "ocr.json")).readtext(b"image")
    assert again == first and Reader.calls == 1


def test_the_test_set_is_complete_and_marked_synthetic():
    docs = B.load_documents()
    assert len(docs) >= 30 and all(d["synthetic"] for d in docs)
    assert {d["format"] for d in docs} == {"png", "scanned_pdf", "text_pdf"}
    assert any(d["expected_findings"] for d in docs) and any(d["unknown_to_dictionary"] for d in docs)
    for d in docs:
        assert os.path.exists(d["path"])
