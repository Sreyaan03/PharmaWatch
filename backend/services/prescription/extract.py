# backend/services/prescription/extract.py
# One interface, interchangeable extractors:
#   LocalExtractor        PDF text layer when there is one, OCR (EasyOCR) when there is not, then the
#                         dictionary-and-rules parser. Works offline; nothing leaves the machine.
#   CloudVisionExtractor  optional; used only when ANTHROPIC_API_KEY is set and RX_EXTRACTOR allows it.
# The result always names which one ran (`extractor`), so the UI can disclose it.

import logging
import os
import statistics

from services.prescription.lexicon import DrugLexicon
from services.prescription.parse import parse_medications
from services.prescription.schema import ExtractionResult, SchemaError, result_from_dict, tool_schema
from core import config

log = logging.getLogger(__name__)

MAX_PAGES = 10
OCR_DPI = 200
VISION_DPI = 150
TEXT_LAYER_MIN_CHARS = 40
LOW_OCR_CONFIDENCE = 0.3
MIME_BY_EXT = {"pdf": "application/pdf", "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
               "txt": "text/plain"}

_lexicon_cache = {}


def default_lexicon():
    """Drug dictionary from the loaded FAERS identity table (cached per database file version)."""
    from faers.analytics import DB_PATH
    key = (DB_PATH, os.path.getmtime(DB_PATH) if os.path.exists(DB_PATH) else 0)
    if key not in _lexicon_cache:
        _lexicon_cache.clear()
        _lexicon_cache[key] = DrugLexicon.from_database(DB_PATH)
    return _lexicon_cache[key]


def mime_for(filename):
    ext = filename.rsplit(".", 1)[-1].lower() if "." in (filename or "") else ""
    return MIME_BY_EXT.get(ext)


def group_ocr_lines(results):
    """EasyOCR [(bbox, text, confidence)] -> [(line text, mean confidence)], top to bottom, left to right."""
    boxes = []
    for bbox, text, conf in results:
        ys = [p[1] for p in bbox]
        xs = [p[0] for p in bbox]
        boxes.append({"y": sum(ys) / len(ys), "x": min(xs), "h": max(ys) - min(ys), "text": text, "conf": float(conf)})
    if not boxes:
        return []
    boxes.sort(key=lambda b: b["y"])
    tolerance = 0.6 * statistics.median(b["h"] for b in boxes)
    lines, current = [], [boxes[0]]
    for b in boxes[1:]:
        if abs(b["y"] - statistics.mean(c["y"] for c in current)) <= tolerance:
            current.append(b)
        else:
            lines.append(current)
            current = [b]
    lines.append(current)
    out = []
    for group in lines:
        group.sort(key=lambda b: b["x"])
        out.append((" ".join(b["text"] for b in group), sum(b["conf"] for b in group) / len(group)))
    return out


class LocalExtractor:
    name = "local_ocr"

    def __init__(self, lexicon=None, reader=None):
        self._lexicon = lexicon
        self._reader = reader

    @property
    def lexicon(self):
        return self._lexicon if self._lexicon is not None else default_lexicon()

    def _ocr(self, png_bytes):
        reader = self._reader
        if reader is None:
            from ml.ocr import get_easyocr_reader
            reader = self._reader = get_easyocr_reader()
        return group_ocr_lines(reader.readtext(png_bytes))

    def _collect(self, data, mime, report):
        """([(line, confidence)], pages, source, unreadable)"""
        if mime == "text/plain":
            return [(l, 1.0) for l in data.decode("utf-8", errors="ignore").splitlines()], 1, "plain_text", []
        if mime in ("image/png", "image/jpeg"):
            report("OCR: reading the image...")
            lines = self._ocr(data)
            return lines, 1, "ocr", [t for t, c in lines if c < LOW_OCR_CONFIDENCE and len(t) >= 3]
        if mime == "application/pdf":
            import fitz
            doc = fitz.open(stream=data, filetype="pdf")
            pages = min(len(doc), MAX_PAGES)
            lines, unreadable, sources = [], [], set()
            for i in range(pages):
                page = doc[i]
                text = page.get_text("text")
                if len(text.strip()) >= TEXT_LAYER_MIN_CHARS:
                    report(f"Reading text of page {i + 1}/{pages}...")
                    lines += [(l, 1.0) for l in text.splitlines()]
                    sources.add("text_layer")
                else:
                    report(f"OCR: transcribing page {i + 1}/{pages}...")
                    page_lines = self._ocr(page.get_pixmap(dpi=OCR_DPI).tobytes("png"))
                    lines += page_lines
                    unreadable += [f"page {i + 1}: {t}" for t, c in page_lines
                                   if c < LOW_OCR_CONFIDENCE and len(t) >= 3]
                    sources.add("ocr")
            source = "ocr" if "ocr" in sources else "text_layer"
            return lines, pages, source, unreadable
        raise ValueError(f"unsupported file type: {mime}")

    def extract(self, data, mime, report=lambda text: None):
        lines, pages, source, unreadable = self._collect(data, mime, report)
        report("Reading medication lines...")
        text = "\n".join(t for t, _ in lines)
        confidences = {t.strip(" \t-•*"): c for t, c in lines}
        meds = parse_medications(text, self.lexicon, line_confidences=confidences)
        return ExtractionResult(
            lines=meds, unreadable_regions=unreadable[:10], pages=pages, text_source=source,
            extractor="local_ocr" if source == "ocr" else "local_text")


class CloudVisionExtractor:
    name = "cloud_vision"

    def __init__(self, client=None, lexicon=None):
        from clients.vision import VisionClient
        self.client = client or VisionClient()
        self._lexicon = lexicon

    def _images(self, data, mime):
        if mime in ("image/png", "image/jpeg"):
            return [(mime, data)]
        if mime == "application/pdf":
            import fitz
            doc = fitz.open(stream=data, filetype="pdf")
            return [("image/png", doc[i].get_pixmap(dpi=VISION_DPI).tobytes("png"))
                    for i in range(min(len(doc), MAX_PAGES))]
        raise ValueError(f"the cloud extractor reads images and PDFs, not {mime}")

    def extract(self, data, mime, report=lambda text: None, job_ref=""):
        from clients.vision import VisionError
        report("Sending page images to the cloud extractor...")
        images = self._images(data, mime)
        payload = self.client.extract(images, tool_schema(), job_ref=job_ref)
        for line in payload.get("lines", []):
            if isinstance(line, dict):
                line.setdefault("confidence", 0.5)
        payload.update(extractor="cloud_vision", text_source="vision", pages=len(images))
        try:
            result = result_from_dict(payload)
        except SchemaError as exc:
            raise VisionError(f"vision response did not match the schema: {exc}") from exc
        lexicon = self._lexicon if self._lexicon is not None else default_lexicon()
        for line in result.lines:                      # add the dictionary match the local path gives
            if line.ingredient is None:
                found = lexicon.find(line.drug_text)
                if found:
                    line.ingredient, line.match = found[0]["ingredient"], found[0]["match"]
        return result


def get_extractor():
    """Cloud when a key is configured and RX_EXTRACTOR allows it, otherwise local (works offline)."""
    mode = config.get("RX_EXTRACTOR").lower()
    if mode != "local" and config.get("ANTHROPIC_API_KEY"):
        return CloudVisionExtractor()
    return LocalExtractor()

