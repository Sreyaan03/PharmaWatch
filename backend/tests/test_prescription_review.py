"""
Prescription review: composition of the existing services, the confirmation gate, and the report.

The review logic is tested with fake dependencies (no network); the workflow endpoints run on real
durable jobs with the extractor and services replaced.
"""

import io
import json
import os
import sys
import time

import pytest

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from core import jobs  # noqa: E402
from services.prescription import report as report_mod  # noqa: E402
from services.prescription import review as R  # noqa: E402
from services.prescription import service as S  # noqa: E402
from services.prescription.extract import LocalExtractor  # noqa: E402
from services.prescription.lexicon import DrugLexicon  # noqa: E402

RXCUI = {"warfarin": "11289", "aspirin": "1191", "metformin": "6809", "atorvastatin": "83367",
         "simvastatin": "36567", "ibuprofen": "5640", "lisinopril": "29046"}
CLASSES = {"atorvastatin": ["HMG-CoA Reductase Inhibitor [EPC]"], "simvastatin": ["HMG-CoA Reductase Inhibitor [EPC]"],
           "aspirin": ["Nonsteroidal Anti-inflammatory Drug [EPC]"]}
BOXED = {"warfarin": "WARNING: BLEEDING RISK. Warfarin sodium can cause major or fatal bleeding.",
         "ibuprofen": "WARNING: RISK OF SERIOUS CARDIOVASCULAR AND GASTROINTESTINAL EVENTS."}


def fake_deps(pair_tiers=None, calls=None):
    calls = calls if calls is not None else []

    def resolve(name):
        key = name.lower()
        if key in RXCUI:
            return {"resolved": True, "ingredients": [{"rxcui": RXCUI[key], "name": key}]}
        return {"resolved": False, "ingredients": []}

    def label(ingredient):
        return {"found": True, "pharm_class": CLASSES.get(ingredient, []), "boxed_warning": BOXED.get(ingredient)}

    def profile(rxcuis):
        name = next(k for k, v in RXCUI.items() if v == rxcuis[0])
        sig = [{"pt": "Haemorrhage", "cases": 400, "prr": 12.0, "ic025": 3.0, "signal_strength": "strong_signal"}] \
            if name == "warfarin" else []
        return {"exposed_cases": 6000, "reactions": [{"pt": "Nausea", "cases": 100}], "signals": sig}

    def pairs(names):
        calls.append(list(names))
        out = []
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                key = frozenset((names[i], names[j]))
                tier = (pair_tiers or {}).get(key, "no_evidence_found")
                out.append({"drug_a": names[i], "drug_b": names[j], "tier": tier,
                            "interaction_likelihood_score": 0.83, "calibrated": True,
                            "label_documented": tier == "documented",
                            "label_snippet": "Concomitant use increases bleeding risk." if tier == "documented" else "",
                            "label_found_in": "drug_interactions", "label_severity": "major", "faers_reports": 120,
                            "model_version": "ddi-gine-42"})
        return {"pairs": out, "errors": []}

    return R.Deps(resolve_drug=resolve, label=label, profile=profile, pairs=pairs)


def L(text, **kw):
    return {"drug_text": text, "confidence": 0.9, "status": "active", **kw}


# ── review logic ─────────────────────────────────────────────────────────────
def test_three_drugs_give_three_pairs_and_three_sections():
    out = R.review([L("warfarin"), L("aspirin"), L("metformin")], fake_deps())
    assert len(out["pairs"]) == 3 and [s["ingredient"] for s in out["drug_sections"]] == ["warfarin", "aspirin", "metformin"]
    assert out["counts"]["boxed_warning"] == 1


def test_the_same_ingredient_on_two_lines_is_duplicate_therapy():
    out = R.review([L("warfarin", strength="5 mg"), L("Warfarin", strength="2 mg"), L("metformin")], fake_deps())
    dup = [f for f in out["findings"] if f["category"] == "duplicate_therapy"]
    assert len(dup) == 1 and "2 lines" in dup[0]["title"]
    assert len(out["analysed"]) == 2                           # warfarin counted once


def test_different_ingredients_in_one_class_are_flagged_with_the_class():
    out = R.review([L("atorvastatin"), L("simvastatin")], fake_deps())
    dup = [f for f in out["findings"] if f["category"] == "duplicate_therapy"]
    assert len(dup) == 1 and "HMG-CoA Reductase Inhibitor" in dup[0]["detail"] and dup[0]["evidence"]["type"] == "label"


def test_an_allergy_matching_an_active_medication_is_the_first_finding():
    out = R.review([L("aspirin"), L("warfarin"), L("aspirin", status="allergy")], fake_deps())
    assert out["findings"][0]["category"] == "allergy_conflict" and "aspirin" in out["findings"][0]["title"]


def test_stopped_medications_and_allergies_are_not_analysed_as_active():
    deps_calls = []
    out = R.review([L("warfarin"), L("metformin", status="stopped"), L("ibuprofen", status="allergy")],
                   fake_deps(calls=deps_calls))
    assert [s["ingredient"] for s in out["stopped"]] == ["metformin"] and out["allergies"][0]["ingredient"] == "ibuprofen"
    assert out["analysed"] == [{"ingredient": "warfarin", "lines": [0]}]
    assert deps_calls == []                                    # one active drug: no pairs to score


def test_an_unrecognised_name_is_listed_as_not_analysed_with_a_reason():
    out = R.review([L("warfarin"), L("Glycomet"), L("metformin")], fake_deps())
    assert out["not_analysed"] == [{"line": 1, "drug_text": "Glycomet",
                                    "reason": "drug name not recognised; correct it or leave it out"}]
    assert len(out["analysed"]) == 2


def test_the_confirmed_ingredient_is_used_when_the_user_corrected_the_name():
    out = R.review([L("Glycomet", ingredient="metformin"), L("warfarin")], fake_deps())
    assert [a["ingredient"] for a in out["analysed"]] == ["metformin", "warfarin"] and not out["not_analysed"]


def test_findings_are_ordered_by_category_and_numbered():
    tiers = {frozenset(("warfarin", "aspirin")): "documented", frozenset(("warfarin", "ibuprofen")): "model_predicted"}
    out = R.review([L("warfarin"), L("aspirin"), L("ibuprofen"), L("aspirin")], fake_deps(tiers))
    cats = [f["category"] for f in out["findings"]]
    assert cats == sorted(cats, key=R.CATEGORY_ORDER.index)
    assert cats[0] == "documented_interaction" and "model_predicted_interaction" in cats and "signal" in cats
    assert [f["id"] for f in out["findings"]] == list(range(1, len(cats) + 1))
    doc = out["findings"][0]
    assert doc["evidence"]["type"] == "label" and "bleeding" in doc["detail"].lower()


def test_pairs_with_no_evidence_are_listed_but_are_not_findings():
    out = R.review([L("metformin"), L("lisinopril")], fake_deps())
    assert len(out["pairs"]) == 1 and out["pairs"][0]["tier"] == "no_evidence_found"
    assert not [f for f in out["findings"] if "interaction" in f["category"]]


def test_a_model_prediction_carries_its_score_calibration_and_version():
    out = R.review([L("warfarin"), L("ibuprofen")], fake_deps({frozenset(("warfarin", "ibuprofen")): "model_predicted"}))
    f = next(f for f in out["findings"] if f["category"] == "model_predicted_interaction")
    assert f["evidence"]["type"] == "modelled" and f["evidence"]["score"] == 0.83 and f["evidence"]["source"] == "ddi-gine-42"
    assert "hypothesis" in f["category_title"].lower()


def test_the_drug_limit_is_enforced_and_reported():
    names = ["warfarin", "aspirin", "metformin", "atorvastatin", "simvastatin"]
    calls = []
    out = R.review([L(n) for n in names], fake_deps(calls=calls), max_drugs=3)
    assert len(out["analysed"]) == 3 and out["truncated"] == ["atorvastatin", "simvastatin"]
    assert calls == [["warfarin", "aspirin", "metformin"]] and "beyond the limit" in out["summary"]


def test_the_summary_states_only_what_the_findings_contain():
    out = R.review([L("warfarin"), L("aspirin")], fake_deps({frozenset(("warfarin", "aspirin")): "documented"}))
    assert out["counts"]["documented_interaction"] == 1 and out["counts"]["boxed_warning"] == 1
    assert "1 documented interaction" in out["summary"] and "1 boxed warning" in out["summary"]
    assert "2 medications analysed (1 pairs checked)" in out["summary"]
    none = R.review([L("metformin"), L("lisinopril")], fake_deps())
    assert "No findings in the sources used" in none["summary"]


def test_the_word_safe_never_appears_in_a_review_or_its_report():
    out = R.review([L("warfarin"), L("aspirin"), L("metformin")], fake_deps())
    rep = report_mod.build_report([L("warfarin"), L("aspirin"), L("metformin")], out, {"data_version": "v"})
    text = (json.dumps(out) + json.dumps(rep)).lower()
    for phrase in ("safe to combine", "is safe", "are safe", "safe combination"):
        assert phrase not in text
    assert "not a statement that a combination is acceptable" in text or "not a statement" in text


# ── the report and its PDF ───────────────────────────────────────────────────
def _report(reference=None):
    lines = [L("warfarin", strength="5 mg", frequency="once daily"), L("aspirin", strength="75 mg"), L("Glycomet")]
    tiers = {frozenset(("warfarin", "aspirin")): "documented"}
    review = R.review(lines, fake_deps(tiers))
    return report_mod.build_report(lines, review, {"reference": reference, "extractor": "local_text",
                                                    "text_source": "text_layer", "data_version": "FAERS test",
                                                    "model_version": "ddi-gine-42", "cloud_used": False})


def _pdf_text(pdf):
    import fitz
    doc = fitz.open(stream=pdf, filetype="pdf")
    return "\n".join(p.get_text() for p in doc), len(doc)


def test_the_pdf_has_every_section_and_every_finding_of_the_json_report():
    rep = _report("bed 12")
    text, pages = _pdf_text(report_mod.render_pdf(rep))
    for section in ("Prescription safety review", "Summary", "Confirmed medication list", "Findings", "Pairs checked",
                    "Not analysed", "Methods and limitations", "Sources", "Research and education use only"):
        assert section in text, section
    flat = " ".join(text.split())
    for f in rep["findings"]:
        assert f["title"].replace("  ", " ")[:40] in flat, f["title"]
    assert "Glycomet" in text and "drug name not recognised" in text
    assert "reference: bed 12" in text and "processed on this machine" in text and "FAERS test" in text


def test_a_long_report_paginates_and_keeps_every_page_footer():
    rep = _report()
    rep["findings"] = rep["findings"] * 12
    pdf = report_mod.render_pdf(rep)
    text, pages = _pdf_text(pdf)
    assert pages >= 2 and text.count("research use only - page") == pages


def test_the_report_carries_no_identifiers_beyond_the_optional_reference():
    rep = _report(None)
    assert rep["reference"] is None
    blob = json.dumps(rep).lower()
    for field in ("patient name", "dob", "date of birth", "mrn", "prescriber", "address"):
        assert field not in blob.replace("patient's conditions", "")


# ── the workflow: upload -> confirm -> report ────────────────────────────────
LEX = DrugLexicon({"warfarin": "warfarin", "metformin": "metformin", "aspirin": "aspirin"})


@pytest.fixture
def client(tmp_path, monkeypatch):
    from app import app
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(S, "get_extractor", lambda: LocalExtractor(lexicon=LEX))
    monkeypatch.setattr(S, "_identity_resolve", lambda name: fake_deps().resolve_drug(name))
    monkeypatch.setattr(R, "default_deps", lambda: fake_deps({frozenset(("warfarin", "aspirin")): "documented"}))
    app.config["TESTING"] = True
    yield app.test_client()
    jobs.shutdown_pool()


def _upload(client, text="Tab Warfarin 5 mg OD\nTab Aspirin 75 mg OD\nTab Glycomet 500 mg BD", name="rx.txt", ref=""):
    return client.post("/api/prescription", data={"file": (io.BytesIO(text.encode()), name), "reference": ref},
                       content_type="multipart/form-data")


def _wait(fn, want, timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        r = fn()
        if r.status_code in want:
            return r
        time.sleep(0.1)
    raise AssertionError(f"timed out; last {r.status_code}: {r.get_json()}")


def _extracted(client, **kw):
    job_id = _upload(client, **kw).get_json()["job_id"]
    end = time.time() + 15
    while time.time() < end:
        body = client.get(f"/api/prescription/{job_id}").get_json()
        if body["status"] in ("done", "failed"):
            return job_id, body
        time.sleep(0.1)
    raise AssertionError("extraction did not finish")


def test_extraction_shows_each_line_with_the_matched_ingredient_next_to_it(client):
    job_id, body = _extracted(client)
    assert body["status"] == "done" and body["confirmed"] is False
    lines = {l["drug_text"]: l for l in body["extraction"]["lines"]}
    assert lines["Warfarin"]["resolution"] == {"resolved": True, "ingredients": ["warfarin"]}
    assert lines["Glycomet"]["resolution"]["resolved"] is False
    assert body["extraction"]["extractor"] == "local_text" and body["extraction"]["cloud_used"] is False


def test_no_report_exists_before_confirmation_gate(client):
    job_id, _ = _extracted(client)
    for path in ("report", "report.pdf"):
        r = client.get(f"/api/prescription/{job_id}/{path}")
        assert r.status_code == 409 and "Confirm" in r.get_json()["error"]


def test_confirmation_starts_the_review_and_the_report_arrives(client):
    job_id, body = _extracted(client, ref="ward 3")
    lines = [dict(l) for l in body["extraction"]["lines"]]
    for l in lines:
        l.pop("resolution", None)
    lines[2]["ingredient"] = "metformin"                      # the user corrects the unknown brand
    r = client.post(f"/api/prescription/{job_id}/confirm", json={"lines": lines})
    assert r.status_code == 202 and r.get_json()["confirmed_lines"] == 3
    rep = _wait(lambda: client.get(f"/api/prescription/{job_id}/report"), want=(200, 500)).get_json()
    assert rep["reference"] == "ward 3" and len(rep["medications"]) == 3
    assert rep["findings"][0]["category"] == "documented_interaction"
    assert {a["ingredient"] for a in [{"ingredient": s["ingredient"]} for s in rep["drug_sections"]]} == {"warfarin", "aspirin", "metformin"}
    assert rep["meta"]["evidence"] == "composite"
    pdf = _wait(lambda: client.get(f"/api/prescription/{job_id}/report.pdf"), want=(200,))
    assert pdf.mimetype == "application/pdf" and pdf.data[:4] == b"%PDF"


def test_extraction_and_confirmation_are_stored_separately(client):
    job_id, body = _extracted(client)
    lines = [{k: v for k, v in l.items() if k != "resolution"} for l in body["extraction"]["lines"]]
    lines[0]["strength"] = "2.5 mg"                           # the user's correction
    client.post(f"/api/prescription/{job_id}/confirm", json={"lines": lines})
    job = jobs.get(job_id)
    assert job["result"]["lines"][0]["strength"] == "5 mg"                       # extraction untouched
    assert job["meta"]["confirmed"]["lines"][0]["strength"] == "2.5 mg"          # confirmation kept apart


def test_bad_confirmations_are_rejected(client):
    job_id, body = _extracted(client)
    for payload in ({}, {"lines": []}, {"lines": ["x"]}, {"lines": [{"strength": "5 mg", "confidence": 0.5}]},
                    {"lines": [{"drug_text": "x", "confidence": 5}]}):
        assert client.post(f"/api/prescription/{job_id}/confirm", json=payload).status_code == 400
    assert client.post("/api/prescription/nope/confirm", json={"lines": [L("x")]}).status_code == 404


def test_uploads_are_validated_and_leave_nothing_on_disk(client):
    assert client.post("/api/prescription", data={}, content_type="multipart/form-data").status_code == 400
    assert _upload(client, name="x.exe").status_code == 400
    assert _upload(client, text="", name="empty.txt").status_code == 400
    big = client.post("/api/prescription", data={"file": (io.BytesIO(b"0" * (jobs.MAX_UPLOAD_BYTES + 1)), "a.pdf")},
                      content_type="multipart/form-data")
    assert big.status_code == 413
    job_id, _ = _extracted(client)
    assert not os.path.exists(os.path.join(jobs.uploads_root(), job_id)), "upload kept after extraction"


def test_a_job_can_be_erased_at_once(client):
    job_id, body = _extracted(client)
    lines = [{k: v for k, v in l.items() if k != "resolution"} for l in body["extraction"]["lines"]]
    client.post(f"/api/prescription/{job_id}/confirm", json={"lines": lines})
    assert client.delete(f"/api/prescription/{job_id}").get_json()["deleted"] is True
    assert client.get(f"/api/prescription/{job_id}").status_code == 404
    assert client.delete(f"/api/prescription/{job_id}").status_code == 404
