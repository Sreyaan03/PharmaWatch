"""
Prescription extraction: interchangeable extractors behind one schema, local by default.

No test here makes a real API call. The local OCR test runs EasyOCR on an image generated in the
test and is skipped where the OCR model is not available.
"""

import io
import os
import sys

import pytest
import requests

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from clients.vision import TOOL_NAME, VisionClient, VisionError  # noqa: E402
from services.prescription import extract as X  # noqa: E402
from services.prescription.lexicon import DrugLexicon  # noqa: E402
from services.prescription.schema import (ExtractionResult, SchemaError, result_from_dict,  # noqa: E402
                                          tool_schema)

LEX = DrugLexicon({"metformin": "metformin", "warfarin": "warfarin", "coumadin": "warfarin",
                   "amlodipine": "amlodipine", "atorvastatin": "atorvastatin"})
RX_TEXT = ["Tab. Metformin 500 mg PO BD x 30 days", "Tab. Amlodipine 5 mg OD", "Cap. Atorvastatin 20 mg HS"]


def pdf_with_text(lines):
    import fitz
    doc = fitz.open()
    page = doc.new_page()
    y = 72
    for line in lines:
        page.insert_text((72, y), line, fontsize=12)
        y += 20
    return doc.tobytes()


# ── local: text layer and plain text ─────────────────────────────────────────
def test_a_pdf_with_a_text_layer_is_read_without_ocr():
    class NoOcr:
        def readtext(self, *_a, **_k):
            raise AssertionError("OCR must not run when the PDF has a text layer")

    result = X.LocalExtractor(lexicon=LEX, reader=NoOcr()).extract(pdf_with_text(RX_TEXT), "application/pdf")
    assert (result.extractor, result.text_source, result.pages) == ("local_text", "text_layer", 1)
    assert [l.ingredient for l in result.lines] == ["metformin", "amlodipine", "atorvastatin"]
    assert result.lines[0].frequency == "twice daily" and result.lines[0].duration == "30 days"


def test_plain_text_is_read_directly():
    result = X.LocalExtractor(lexicon=LEX).extract("\n".join(RX_TEXT).encode(), "text/plain")
    assert result.text_source == "plain_text" and len(result.lines) == 3


def test_an_unsupported_type_is_refused():
    with pytest.raises(ValueError):
        X.LocalExtractor(lexicon=LEX).extract(b"x", "application/zip")


def test_a_scanned_page_goes_through_ocr_and_is_labelled_so():
    import fitz

    class FakeReader:
        calls = 0

        def readtext(self, png):
            FakeReader.calls += 1
            box = lambda y: [[10, y], [200, y], [200, y + 18], [10, y + 18]]       # noqa: E731
            return [(box(20), "Tab. Warfarin", 0.9), ([[210, 20], [300, 20], [300, 38], [210, 38]], "5 mg OD", 0.8),
                    (box(60), "Tab. Metformin 500 mg BD", 0.2)]

    doc = fitz.open()
    doc.new_page()                              # an empty page: no text layer
    result = X.LocalExtractor(lexicon=LEX, reader=FakeReader()).extract(doc.tobytes(), "application/pdf")
    assert FakeReader.calls == 1 and (result.extractor, result.text_source) == ("local_ocr", "ocr")
    assert [l.ingredient for l in result.lines] == ["warfarin", "metformin"]
    assert result.lines[0].frequency == "once daily"                  # two boxes joined into one line
    assert result.lines[1].confidence < result.lines[0].confidence    # the poorly read line is less sure
    assert any("Metformin" in r for r in result.unreadable_regions)


def test_ocr_boxes_are_grouped_into_lines_top_to_bottom_left_to_right():
    box = lambda x, y: [[x, y], [x + 50, y], [x + 50, y + 20], [x, y + 20]]       # noqa: E731
    lines = X.group_ocr_lines([(box(120, 12), "500 mg", 0.9), (box(10, 10), "Metformin", 0.7), (box(10, 60), "Warfarin", 0.8)])
    assert [t for t, _ in lines] == ["Metformin 500 mg", "Warfarin"]
    assert lines[0][1] == pytest.approx(0.8)


def _real_reader():
    # Loading the model from scratch downloads ~100 MB; only run where it is already on disk.
    if not os.path.exists(os.path.expanduser("~/.EasyOCR/model/craft_mlt_25k.pth")):
        pytest.skip("EasyOCR model not downloaded")
    try:
        from ml.ocr import get_easyocr_reader
        return get_easyocr_reader()
    except Exception as exc:                                         # model not downloaded, no network
        pytest.skip(f"EasyOCR unavailable: {exc}")


def test_real_ocr_reads_a_generated_prescription_image():
    from PIL import Image, ImageDraw, ImageFont
    reader = _real_reader()
    img = Image.new("RGB", (900, 220), "white")
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default(size=34)
    for i, line in enumerate(["Tab. Metformin 500 mg BD", "Tab. Warfarin 5 mg OD"]):
        draw.text((30, 20 + 80 * i), line, fill="black", font=font)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    result = X.LocalExtractor(lexicon=LEX, reader=reader).extract(buf.getvalue(), "image/png")
    names = {l.ingredient for l in result.lines}
    assert {"metformin", "warfarin"} <= names, [l.drug_text for l in result.lines]


# ── the schema ───────────────────────────────────────────────────────────────
GOOD = {"lines": [{"drug_text": "Warfarin", "strength": "5 mg", "confidence": 0.9, "frequency": "once daily"}],
        "unreadable_regions": ["bottom corner"], "extractor": "cloud_vision", "text_source": "vision", "pages": 1}


def test_a_valid_payload_parses_and_extractors_share_one_result_type():
    r = result_from_dict(GOOD)
    local = X.LocalExtractor(lexicon=LEX).extract("Warfarin 5 mg".encode(), "text/plain")
    assert isinstance(r, ExtractionResult) and isinstance(local, ExtractionResult)
    assert r.lines[0].status == "active" and r.unreadable_regions == ["bottom corner"]


@pytest.mark.parametrize("bad", [
    {"lines": "nope"}, {"lines": [{"strength": "5 mg", "confidence": 0.5}]},
    {"lines": [{"drug_text": "x", "confidence": 1.7}]}, {"lines": [{"drug_text": "x", "confidence": 0.5, "dose_mg": 5}]},
    {"lines": [{"drug_text": "x", "confidence": 0.5, "status": "maybe"}]}, ["not", "an", "object"],
    {"lines": [], "extractor": "magic"},
])
def test_malformed_payloads_raise_a_schema_error(bad):
    with pytest.raises(SchemaError):
        result_from_dict(bad)


def test_the_tool_schema_matches_the_dataclass_fields():
    props = tool_schema()["properties"]["lines"]["items"]["properties"]
    from services.prescription.schema import LINE_FIELDS
    assert set(props) <= set(LINE_FIELDS) and {"drug_text", "confidence", "status"} <= set(props)


# ── cloud client, with the API replaced ──────────────────────────────────────
class FakeResp:
    def __init__(self, status, payload=None):
        self.status_code, self._p = status, payload or {}

    def json(self):
        return self._p


class FakeSession:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def post(self, url, headers=None, json=None, timeout=None):
        self.calls.append({"url": url, "headers": headers, "json": json})
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def tool_reply(payload):
    return FakeResp(200, {"content": [{"type": "tool_use", "name": TOOL_NAME, "input": payload}]})


def client(*responses, retries=1):
    s = FakeSession(*responses)
    return VisionClient(api_key="test-key", session=s, sleep=lambda x: None, retries=retries), s


def test_a_cloud_reply_becomes_the_same_schema():
    c, session = client(tool_reply({"lines": [{"drug_text": "Warfarin", "confidence": 0.8}]}))
    ex = X.CloudVisionExtractor(client=c, lexicon=LEX)
    result = ex.extract(pdf_with_text(["x"]), "application/pdf")
    assert (result.extractor, result.text_source) == ("cloud_vision", "vision")
    assert result.lines[0].ingredient == "warfarin"                  # dictionary match added
    sent = session.calls[0]
    assert sent["headers"]["x-api-key"] == "test-key" and sent["json"]["tool_choice"]["name"] == TOOL_NAME
    assert sent["json"]["messages"][0]["content"][0]["type"] == "image"
    assert "Do not transcribe patient" in sent["json"]["messages"][0]["content"][-1]["text"]


def test_a_malformed_cloud_reply_raises_the_typed_error():
    for reply in (FakeResp(200, {"content": [{"type": "text", "text": "I cannot"}]}),
                  tool_reply({"lines": [{"confidence": 0.5}]}),
                  tool_reply({"lines": [{"drug_text": "x", "confidence": 9}]})):
        c, _ = client(reply)
        with pytest.raises(VisionError):
            X.CloudVisionExtractor(client=c, lexicon=LEX).extract(pdf_with_text(["x"]), "application/pdf")


def test_server_errors_are_retried_once_then_raise():
    c, session = client(FakeResp(503), tool_reply({"lines": []}))
    assert X.CloudVisionExtractor(client=c, lexicon=LEX).extract(pdf_with_text(["x"]), "application/pdf").lines == []
    assert len(session.calls) == 2
    c, session = client(FakeResp(503), FakeResp(503))
    with pytest.raises(VisionError, match="HTTP 503"):
        c.extract([("image/png", b"x")], tool_schema())
    c, session = client(FakeResp(401))
    with pytest.raises(VisionError):
        c.extract([("image/png", b"x")], tool_schema())
    assert len(session.calls) == 1                                    # 401 is not retried


def test_network_failures_raise_the_typed_error():
    c, _ = client(requests.ConnectionError("down"), requests.Timeout("slow"))
    with pytest.raises(VisionError, match="network error"):
        c.extract([("image/png", b"x")], tool_schema())


def test_the_client_refuses_to_start_without_a_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(VisionError):
        VisionClient()


# ── choosing the extractor ───────────────────────────────────────────────────
def test_without_a_key_extraction_is_local(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert isinstance(X.get_extractor(), X.LocalExtractor)


def test_with_a_key_the_cloud_extractor_is_used_unless_local_is_forced(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    assert isinstance(X.get_extractor(), X.CloudVisionExtractor)
    monkeypatch.setenv("RX_EXTRACTOR", "local")
    assert isinstance(X.get_extractor(), X.LocalExtractor)
