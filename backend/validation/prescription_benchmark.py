"""
Prescription benchmark: how accurately are medication lines extracted, and does the review find
the planted problems?

    python -m validation.prescription_benchmark --extractor local            # OCR, real drug dictionary
    python -m validation.prescription_benchmark --smoke                      # text-layer PDFs only, fixture dictionary (CI)
    python -m validation.prescription_benchmark --extractor cloud            # needs ANTHROPIC_API_KEY; paid; run by hand
    python -m validation.prescription_benchmark --review                     # also score the review (needs network)

Test set: backend/tests/fixtures/prescriptions (synthetic, printed). Handwritten samples, when
faculty add them, are reported separately and never mixed in. Results here describe printed text:
they say nothing about handwriting.

Extraction is scored per line and per field after normalisation. A line is `matched` when its drug
text (or, failing that, its ingredient) equals a ground-truth line; ground-truth lines never found
are `missed`; extracted lines matching nothing are `invented`. Field accuracy counts only
fields the ground truth has; a wrong non-empty value is reported apart from an empty one.
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone

FIXTURES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tests", "fixtures", "prescriptions")
REPORTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                           "reports", "validation")
MIME = {"png": "image/png", "jpg": "image/jpeg", "pdf": "application/pdf"}
FIELDS = ("ingredient", "strength", "frequency", "duration", "route")


def load_documents(directory=FIXTURES, only_formats=None):
    manifest = json.load(open(os.path.join(directory, "manifest.json"), encoding="utf-8"))
    docs = []
    for d in manifest["documents"]:
        if only_formats and d["format"] not in only_formats:
            continue
        gt = json.load(open(os.path.join(directory, d["id"] + ".json"), encoding="utf-8"))
        docs.append({**gt, "path": os.path.join(directory, d["file"])})
    return docs


def fixture_lexicon(directory=FIXTURES):
    from services.prescription.lexicon import DrugLexicon
    return DrugLexicon(json.load(open(os.path.join(directory, "lexicon_fixture.json"), encoding="utf-8")))


class CachingReader:
    """Wraps an EasyOCR reader and remembers its output per image, so a rerun of the benchmark
    after a parser change does not repeat minutes of OCR. The cache is keyed by image hash."""

    def __init__(self, reader, path):
        self.reader, self.path, self.cache = reader, path, {}
        if path and os.path.exists(path):
            self.cache = json.load(open(path, encoding="utf-8"))

    def readtext(self, data):
        import hashlib
        key = hashlib.sha256(data).hexdigest()
        if key not in self.cache:
            raw = self.reader.readtext(data)
            self.cache[key] = [[[[float(x), float(y)] for x, y in bbox], text, float(conf)] for bbox, text, conf in raw]
            if self.path:
                json.dump(self.cache, open(self.path, "w", encoding="utf-8"))
        return [(b, t, c) for b, t, c in self.cache[key]]


# ── normalisation and scoring ────────────────────────────────────────────────
def _n(value):
    return re.sub(r"\s+", "", str(value or "").lower())


def norm_ingredient(v):
    return re.sub(r"\s+", " ", str(v or "").lower()).strip()


def norm_value(field, v):
    if v in (None, ""):
        return None
    if field == "ingredient":
        return norm_ingredient(v)
    return _n(v)


def align(gt_lines, ext_lines):
    """[(gt index, extracted index | None)] and the extracted indexes nothing matched (invented)."""
    used, pairs = set(), []
    for gi, g in enumerate(gt_lines):
        hit = next((ei for ei, e in enumerate(ext_lines)
                    if ei not in used and _n(e["drug_text"]) == _n(g["drug_text"])), None)
        if hit is None and g.get("ingredient"):
            hit = next((ei for ei, e in enumerate(ext_lines)
                        if ei not in used and norm_ingredient(e.get("ingredient")) == norm_ingredient(g["ingredient"])), None)
        if hit is not None:
            used.add(hit)
        pairs.append((gi, hit))
    return pairs, [ei for ei in range(len(ext_lines)) if ei not in used]


def score_document(gt_lines, ext_lines):
    pairs, invented = align(gt_lines, ext_lines)
    out = {"gt_lines": len(gt_lines), "extracted_lines": len(ext_lines),
           "matched": sum(1 for _, e in pairs if e is not None), "invented": len(invented),
           "fields": {f: {"expected": 0, "correct": 0, "wrong": 0, "empty": 0} for f in FIELDS},
           "status": {"expected": 0, "correct": 0}, "unrecognised": {"wrong_ingredient": 0, "flagged": 0},
           "details": []}
    for gi, ei in pairs:
        if ei is None:
            out["details"].append({"line": gt_lines[gi]["drug_text"], "problem": "missed"})
            continue
        g, e = gt_lines[gi], ext_lines[ei]
        for f in FIELDS:
            want = norm_value(f, g.get(f))
            if want is None:
                continue
            got = norm_value(f, e.get(f))
            cell = out["fields"][f]
            cell["expected"] += 1
            if got == want:
                cell["correct"] += 1
            else:
                cell["empty" if got is None else "wrong"] += 1
                out["details"].append({"line": g["drug_text"], "problem": "empty" if got is None else "wrong",
                                       "field": f, "expected": g.get(f), "got": e.get(f)})
        out["status"]["expected"] += 1
        out["status"]["correct"] += int(e.get("status", "active") == g.get("status", "active"))
        if norm_value("ingredient", g.get("ingredient")) != norm_value("ingredient", e.get("ingredient")):
            out["unrecognised"]["wrong_ingredient"] += 1
            out["unrecognised"]["flagged"] += int(e.get("match") in (None, "fuzzy") or e.get("confidence", 1) < 0.6)
    for ei in invented:
        out["details"].append({"line": ext_lines[ei]["drug_text"], "problem": "invented"})
    return out


def merge(scores):
    total = {"gt_lines": 0, "extracted_lines": 0, "matched": 0, "invented": 0,
             "fields": {f: {"expected": 0, "correct": 0, "wrong": 0, "empty": 0} for f in FIELDS},
             "status": {"expected": 0, "correct": 0}, "unrecognised": {"wrong_ingredient": 0, "flagged": 0}}
    for s in scores:
        for k in ("gt_lines", "extracted_lines", "matched", "invented"):
            total[k] += s[k]
        for f in FIELDS:
            for k in total["fields"][f]:
                total["fields"][f][k] += s["fields"][f][k]
        for k in total["status"]:
            total["status"][k] += s["status"][k]
        for k in total["unrecognised"]:
            total["unrecognised"][k] += s["unrecognised"][k]
    return total


def summarise(total):
    def rate(a, b):
        return round(a / b, 4) if b else None
    out = {"documents_lines": total["gt_lines"], "line_recall": rate(total["matched"], total["gt_lines"]),
           "line_precision": rate(total["matched"], total["extracted_lines"]),
           "lines_missed": total["gt_lines"] - total["matched"], "lines_invented": total["invented"],
           "status_accuracy": rate(total["status"]["correct"], total["status"]["expected"])}
    for f in FIELDS:
        c = total["fields"][f]
        out[f"{f}_accuracy"] = rate(c["correct"], c["expected"])
        out[f"{f}_wrong_value"] = c["wrong"]
        out[f"{f}_left_empty"] = c["empty"]
    u = total["unrecognised"]
    out["wrong_ingredients"] = u["wrong_ingredient"]
    out["wrong_ingredients_flagged_for_checking"] = rate(u["flagged"], u["wrong_ingredient"])
    return out


# ── review scoring ───────────────────────────────────────────────────────────
def finding_present(expected, findings):
    want = set(expected["ingredients"])
    for f in findings:
        if f["category"] != expected["category"]:
            continue
        title = f["title"].lower()
        if all(w in title or w in f["detail"].lower() for w in want):
            return True
    return False


def score_review(docs, deps=None):
    from services.prescription import review as R
    deps = deps or R.default_deps()
    per_cat, detail = {}, []
    for d in docs:
        if not d["expected_findings"]:
            continue
        out = R.review([{k: v for k, v in l.items()} for l in d["lines"]], deps)
        for exp in d["expected_findings"]:
            hit = finding_present(exp, out["findings"])
            c = per_cat.setdefault(exp["category"], {"expected": 0, "found": 0})
            c["expected"] += 1
            c["found"] += int(hit)
            if not hit:
                detail.append({"doc": d["id"], "missed": exp})
    total = {"expected": sum(c["expected"] for c in per_cat.values()), "found": sum(c["found"] for c in per_cat.values())}
    return {"by_category": per_cat, "recall": round(total["found"] / total["expected"], 4) if total["expected"] else None,
            "expected": total["expected"], "found": total["found"], "missed": detail}


# ── the run ──────────────────────────────────────────────────────────────────
def run(extractor="local", smoke=False, review=False, out_path=None, directory=FIXTURES, ocr_cache=None):
    from services.prescription.extract import CloudVisionExtractor, LocalExtractor, default_lexicon
    docs = load_documents(directory, only_formats=("text_pdf",) if smoke else None)
    lexicon = fixture_lexicon(directory) if smoke else default_lexicon()
    reader = None
    if extractor == "local" and not smoke:
        from ml.ocr import get_easyocr_reader
        reader = CachingReader(get_easyocr_reader(), ocr_cache)
    ex = CloudVisionExtractor(lexicon=lexicon) if extractor == "cloud" else LocalExtractor(lexicon=lexicon, reader=reader)
    scores, by_format, by_level, failures, details = [], {}, {}, [], []
    for d in docs:
        mime = MIME[d["path"].rsplit(".", 1)[1]]
        try:
            res = ex.extract(open(d["path"], "rb").read(), mime)
        except Exception as exc:
            failures.append({"doc": d["id"], "error": type(exc).__name__})
            continue
        s = score_document(d["lines"], [l.__dict__ if hasattr(l, "__dict__") else l for l in res.lines])
        scores.append(s)
        details += [{"doc": d["id"], "format": d["format"], "quality": d["degradation"], **x} for x in s["details"]]
        by_format.setdefault(d["format"], []).append(s)
        by_level.setdefault(d["degradation"], []).append(s)

    report = {
        "benchmark": "prescription", "smoke": smoke, "extractor": extractor,
        "model": os.getenv("RX_VISION_MODEL", "claude-opus-5-5") if extractor == "cloud" else "easyocr + rules",
        "run_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "dictionary": "test fixture (parser only)" if smoke else f"FAERS identity table, {len(lexicon):,} names",
        "test_set": {"documents": len(docs), "scored": len(scores), "synthetic": True, "handwritten": 0,
                     "note": "printed synthetic prescriptions; results say nothing about handwriting"},
        "overall": summarise(merge(scores)) if scores else None,
        "by_format": {k: summarise(merge(v)) for k, v in by_format.items()},
        "by_image_quality": {k: summarise(merge(v)) for k, v in by_level.items()},
        "failures": failures,
        "line_problems": details,
    }
    if review:
        try:
            report["review"] = score_review(docs)
        except Exception as exc:
            report["review"] = {"skipped": f"{type(exc).__name__}: {exc}"}
    out_path = out_path or os.path.join(REPORTS_DIR, "prescription_smoke.json" if smoke else
                                        ("prescription_cloud.json" if extractor == "cloud" else "prescription.json"))
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(report, f, indent=2, sort_keys=True)
        f.write("\n")
    return report, out_path


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--extractor", choices=("local", "cloud"), default="local")
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--review", action="store_true")
    ap.add_argument("--ocr-cache", default=os.path.join(os.environ.get("DATA_DIR", ""), "ocr_cache_prescriptions.json")
                    if os.environ.get("DATA_DIR") else None, help="remember OCR output between runs")
    args = ap.parse_args(argv)
    if args.extractor == "cloud" and not os.getenv("ANTHROPIC_API_KEY"):
        print("[-] ANTHROPIC_API_KEY is not set; the cloud benchmark makes paid API calls.", file=sys.stderr)
        return 2
    if not os.path.exists(os.path.join(FIXTURES, "manifest.json")):
        print("[-] prescription test set not found", file=sys.stderr)
        return 2
    report, path = run(args.extractor, smoke=args.smoke, review=args.review, ocr_cache=args.ocr_cache)
    o = report["overall"] or {}
    print({k: o.get(k) for k in ("line_recall", "line_precision", "lines_invented", "ingredient_accuracy",
                                 "strength_accuracy", "frequency_accuracy", "duration_accuracy")})
    print("wrote", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
