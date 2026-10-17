"""
Synthetic prescriptions with ground truth, for measuring extraction and review accuracy.

    python make_prescriptions.py --generate    # writes rx_NNN.(png|pdf) and rx_NNN.json here
    python make_prescriptions.py --check       # every document has a ground-truth file that
                                                 validates against the extraction schema

Everything on a page is invented and every page says so. Documents vary in layout (numbered list,
table-like columns, discharge-summary style), abbreviations (OD, BD, TDS, 1-0-1, HS, PRN), brand
versus generic names, and image quality (clean, mildly degraded, scan-like). A few carry planted
interactions, boxed-warning drugs, duplicate therapy and an allergy conflict so the review can be
scored too. Three formats: PNG, image-only (scanned) PDF, and PDF with a text layer.

`handwritten/` is the place for faculty-supplied handwritten samples, to be added after ethics
approval; this generator never produces handwriting.
"""

import argparse
import io
import json
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..", "..")))

SEED = 2026
N_DOCS = 36
BANNER = "SYNTHETIC SAMPLE - INVENTED DATA - NOT A REAL PRESCRIPTION"

# ingredient: (printed names, kind of each: g = generic, b = brand known to FAERS, u = not in FAERS data)
CATALOG = {
    "warfarin": (["Warfarin", "Coumadin"], ["1 mg", "2 mg", "5 mg"], ["OD", "1-0-0", "once daily"]),
    "aspirin": (["Aspirin", "Ecosprin"], ["75 mg", "81 mg", "150 mg"], ["OD", "once daily"]),
    "metformin": (["Metformin", "Glucophage", "Glycomet"], ["500 mg", "850 mg", "1000 mg"], ["BD", "1-0-1", "twice daily"]),
    "atorvastatin": (["Atorvastatin", "Lipitor"], ["10 mg", "20 mg", "40 mg"], ["HS", "at night"]),
    "simvastatin": (["Simvastatin", "Zocor"], ["20 mg", "40 mg"], ["HS", "at night"]),
    "amlodipine": (["Amlodipine", "Norvasc"], ["5 mg", "10 mg"], ["OD", "1-0-0"]),
    "lisinopril": (["Lisinopril", "Zestril"], ["5 mg", "10 mg", "20 mg"], ["OD", "once daily"]),
    "losartan": (["Losartan", "Cozaar"], ["50 mg"], ["OD"]),
    "clopidogrel": (["Clopidogrel", "Plavix"], ["75 mg"], ["OD", "once daily"]),
    "omeprazole": (["Omeprazole", "Prilosec"], ["20 mg", "40 mg"], ["OD", "BD"]),
    "pantoprazole": (["Pantoprazole", "Protonix"], ["40 mg"], ["OD"]),
    "furosemide": (["Furosemide", "Lasix", "Frusemide"], ["20 mg", "40 mg"], ["OD", "BD"]),
    "levothyroxine": (["Levothyroxine", "Synthroid", "Thyroxine"], ["50 mcg", "100 mcg"], ["OD", "once daily"]),
    "ibuprofen": (["Ibuprofen", "Advil"], ["400 mg", "200 mg"], ["TDS", "1-1-1"]),
    "acetaminophen": (["Paracetamol", "Acetaminophen", "Tylenol"], ["500 mg", "650 mg"], ["TDS", "QID", "q8h"]),
    "amiodarone": (["Amiodarone", "Cordarone"], ["200 mg"], ["OD", "once daily"]),
    "digoxin": (["Digoxin", "Lanoxin"], ["0.25 mg", "0.125 mg"], ["OD"]),
    "azithromycin": (["Azithromycin", "Zithromax"], ["500 mg", "250 mg"], ["OD"]),
    "ciprofloxacin": (["Ciprofloxacin", "Cipro"], ["500 mg", "250 mg"], ["BD", "twice daily"]),
    "amoxicillin": (["Amoxicillin", "Amoxycillin"], ["500 mg", "250 mg"], ["TDS", "1-1-1"]),
    "prednisolone": (["Prednisolone"], ["5 mg", "10 mg", "20 mg"], ["OD", "BD"]),
    "sertraline": (["Sertraline", "Zoloft"], ["50 mg", "100 mg"], ["OD", "once daily"]),
    "gabapentin": (["Gabapentin", "Neurontin"], ["300 mg", "100 mg"], ["TDS", "HS"]),
    "tramadol": (["Tramadol", "Ultram"], ["50 mg"], ["BD", "TDS"]),
    "carvedilol": (["Carvedilol", "Coreg"], ["6.25 mg", "12.5 mg"], ["BD", "twice daily"]),
    "spironolactone": (["Spironolactone", "Aldactone"], ["25 mg"], ["OD"]),
    "rosuvastatin": (["Rosuvastatin", "Crestor"], ["10 mg", "20 mg"], ["HS"]),
    "apixaban": (["Apixaban", "Eliquis"], ["5 mg", "2.5 mg"], ["BD", "twice daily"]),
}
UNKNOWN_TO_FAERS = {"Ecosprin", "Glycomet"}                       # Indian brands: expected to be flagged
CANON_FREQ = {"OD": "once daily", "1-0-0": "once daily", "once daily": "once daily", "BD": "twice daily",
              "1-0-1": "twice daily", "twice daily": "twice daily", "TDS": "three times daily",
              "1-1-1": "three times daily", "q8h": "every 8 hours", "QID": "four times daily",
              "HS": "at bedtime", "at night": "at bedtime"}
DURATIONS = [None, None, "5 days", "7 days", "14 days", "30 days", "3 months"]
# well-known boxed warnings on the US label (the set the review is expected to flag)
BOXED = {"warfarin", "ibuprofen", "ciprofloxacin", "amiodarone", "metformin", "tramadol", "sertraline",
         "apixaban", "clopidogrel", "levothyroxine"}
SCENARIOS = {
    "interaction_warfarin_aspirin": (["warfarin", "aspirin"], [{"category": "documented_interaction", "ingredients": ["warfarin", "aspirin"]}]),
    "interaction_simvastatin_amiodarone": (["simvastatin", "amiodarone"], [{"category": "documented_interaction", "ingredients": ["simvastatin", "amiodarone"]}]),
    "interaction_clopidogrel_omeprazole": (["clopidogrel", "omeprazole"], [{"category": "documented_interaction", "ingredients": ["clopidogrel", "omeprazole"]}]),
    "duplicate_statins": (["atorvastatin", "simvastatin"], [{"category": "duplicate_therapy", "ingredients": ["atorvastatin", "simvastatin"]}]),
    "duplicate_same_ingredient": (["warfarin", "warfarin"], [{"category": "duplicate_therapy", "ingredients": ["warfarin"]}]),
}
STYLES = ["numbered", "table", "discharge"]


def pick_line(rng, ingredient, force_name=None):
    names, strengths, freqs = CATALOG[ingredient]
    name = force_name or rng.choice(names)
    strength, freq = rng.choice(strengths), rng.choice(freqs)
    duration = rng.choice(DURATIONS)
    form = rng.choice(["Tab.", "Tab", "Cap."]) if rng.random() < 0.6 else ""
    prn = ingredient in ("ibuprofen", "acetaminophen", "tramadol") and rng.random() < 0.4
    route = "PO" if rng.random() < 0.25 else ""
    return {"ingredient": ingredient, "name": name, "strength": strength, "freq": freq, "duration": duration,
            "form": form, "prn": prn, "route": route}


def render_line(style, spec, n):
    form, strength = spec["form"], spec["strength"].replace(" ", "") if spec["name"][0] in "AEIOU" and False else spec["strength"]
    dur = f" x {spec['duration']}" if spec["duration"] else ""
    prn = " PRN" if spec["prn"] else ""
    route = f" {spec['route']}" if spec["route"] else ""
    if style == "numbered":
        return f"{n}. {form} {spec['name']} {strength}{route} {spec['freq']}{prn}{dur}".replace("  ", " ").strip()
    if style == "table":
        return f"{spec['name']}     {strength}     {spec['freq']}{prn}     {(spec['duration'] or '').strip()}".rstrip()
    return f"- {form} {spec['name']} {strength}{route} {spec['freq']}{prn}{dur}".replace("  ", " ").strip()


def truth_line(spec, status="active"):
    return {"drug_text": spec["name"], "ingredient": spec["ingredient"], "strength": spec["strength"],
            "dose_form": {"Tab.": "tablet", "Tab": "tablet", "Cap.": "capsule"}.get(spec["form"]),
            "dose": None, "route": "oral" if spec["route"] else None, "frequency": CANON_FREQ[spec["freq"]],
            "duration": spec["duration"], "as_needed": spec["prn"], "confidence": 1.0, "notes": "", "status": status}


def make_document(i, rng):
    style = STYLES[i % 3]
    scenario = None
    ingredients = []
    expected = []
    if i % 4 == 1:                                              # planted scenarios, cycling through them
        key = list(SCENARIOS)[(i // 4) % len(SCENARIOS)]
        scenario = key
        ingredients = list(SCENARIOS[key][0])
        expected = [dict(f) for f in SCENARIOS[key][1]]
    extra = rng.sample([k for k in CATALOG if k not in ingredients], rng.randint(1 if scenario else 3, 3 if scenario else 5))
    ingredients += extra
    rng.shuffle(ingredients)

    specs, force = [], {}
    for ing in ingredients:
        spec = pick_line(rng, ing)
        if scenario == "duplicate_same_ingredient" and ing == "warfarin":
            spec["name"] = "Warfarin" if "w1" not in force else "Coumadin"
            force["w1"] = True
        specs.append(spec)
    truth = [truth_line(s) for s in specs]

    if i % 9 == 4:                                              # allergy conflict + a stopped drug
        ing = specs[0]["ingredient"]
        truth.append({**truth_line(pick_line(rng, ing, force_name=CATALOG[ing][0][0])), "status": "allergy",
                      "strength": None, "frequency": None, "duration": None, "dose_form": None, "route": None})
        expected.append({"category": "allergy_conflict", "ingredients": [ing]})
    boxed_in_list = sorted({s["ingredient"] for s in specs if s["ingredient"] in BOXED})
    for ing in boxed_in_list:
        expected.append({"category": "boxed_warning", "ingredients": [ing]})
    unknown = [s["name"] for s in specs if s["name"] in UNKNOWN_TO_FAERS]
    return style, scenario, specs, truth, expected, unknown


def page_lines(style, specs, truth):
    lines = [BANNER, ""]
    if style == "numbered":
        lines += ["Dr. A. Example, Sample Clinic", "Date: 01/01/2000        Rx", ""]
        lines += [render_line("numbered", s, n) for n, s in enumerate(specs, 1)]
        lines += ["", "Review after 2 weeks"]
    elif style == "table":
        lines += ["Name: Sample Patient          Age: 00", "Drug     Strength     Frequency     Duration"]
        lines += [render_line("table", s, n) for n, s in enumerate(specs, 1)]
        lines += ["", "Advice: take with food"]
    else:
        lines += ["DISCHARGE SUMMARY (synthetic)", "Diagnosis: sample condition", "", "DISCHARGE MEDICATIONS"]
        lines += [render_line("discharge", s, n) for n, s in enumerate(specs, 1)]
        for t in truth:
            if t["status"] == "allergy":
                lines += ["", f"Allergy: {t['drug_text']}"]
        lines += ["", "Follow up in 2 weeks"]
    return lines


def degrade(img, level, rng):
    from PIL import Image, ImageEnhance, ImageFilter
    if level == "clean":
        return img
    angle = rng.uniform(-0.8, 0.8) if level == "mild" else rng.uniform(-1.5, 1.5)
    img = img.rotate(angle, expand=True, fillcolor="white", resample=Image.BICUBIC)
    img = img.filter(ImageFilter.GaussianBlur(0.5 if level == "mild" else 0.9))
    img = ImageEnhance.Contrast(img).enhance(0.9 if level == "mild" else 0.75)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=80 if level == "mild" else 55)
    return Image.open(io.BytesIO(buf.getvalue())).convert("RGB")


def render_image(lines, level, rng):
    from PIL import Image, ImageDraw, ImageFont
    font = ImageFont.load_default(size=22)
    h = 60 + 36 * len(lines)
    img = Image.new("RGB", (1100, h), "white")
    draw = ImageDraw.Draw(img)
    for n, line in enumerate(lines):
        draw.text((40, 30 + 36 * n), line, fill="black", font=font)
    return degrade(img, level, rng)


def encode(img, level, as_jpeg=False):
    """(bytes, extension): clean pages as PNG, degraded (scan-like) pages as JPEG to keep the set small.

    Pages embedded in a scanned PDF are always JPEG: PyMuPDF stores an embedded PNG as raw pixels.
    """
    buf = io.BytesIO()
    if level == "clean" and not as_jpeg:
        img.save(buf, "PNG", optimize=True)
        return buf.getvalue(), "png"
    img.save(buf, "JPEG", quality=85 if level == "clean" else 70)
    return buf.getvalue(), "jpg"


def write_text_pdf(path, lines):
    import fitz
    doc = fitz.open()
    page = doc.new_page()
    y = 60
    for line in lines:
        page.insert_text((50, y), line, fontsize=11)
        y += 18
    doc.save(path)


def write_image_pdf(path, image_bytes, size):
    import fitz
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    scale = 495 / size[0]
    page.insert_image(fitz.Rect(50, 50, 545, 50 + size[1] * scale), stream=image_bytes)
    doc.save(path)


def generate(directory):
    rng = random.Random(SEED)
    manifest = []
    for i in range(N_DOCS):
        doc_rng = random.Random(rng.random())
        style, scenario, specs, truth, expected, unknown = make_document(i, doc_rng)
        fmt = ["png", "scanned_pdf", "text_pdf"][i % 3]
        level = "clean" if fmt == "text_pdf" else ["clean", "mild", "harsh"][(i // 3) % 3]
        name = f"rx_{i + 1:03d}"
        lines = page_lines(style, specs, truth)
        if fmt == "text_pdf":
            ext = "pdf"
            write_text_pdf(os.path.join(directory, f"{name}.{ext}"), lines)
        else:
            img = render_image(lines, level, doc_rng)
            data, img_ext = encode(img, level, as_jpeg=(fmt == "scanned_pdf"))
            ext = img_ext if fmt == "png" else "pdf"
            path = os.path.join(directory, f"{name}.{ext}")
            if fmt == "png":
                open(path, "wb").write(data)
            else:
                write_image_pdf(path, data, img.size)
        gt = {"id": name, "file": f"{name}.{ext}", "format": fmt, "style": style, "degradation": level,
              "scenario": scenario, "synthetic": True, "lines": truth, "expected_findings": expected,
              "unknown_to_dictionary": unknown}
        with open(os.path.join(directory, f"{name}.json"), "w", encoding="utf-8", newline="\n") as f:
            json.dump(gt, f, indent=2)
        manifest.append({k: gt[k] for k in ("id", "file", "format", "style", "degradation", "scenario")})
    with open(os.path.join(directory, "manifest.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump({"seed": SEED, "documents": manifest}, f, indent=2)
    # the dictionary used by the CI smoke benchmark: every printed name the FAERS data knows
    lexicon = {name.lower(): ing for ing, (names, _, _) in CATALOG.items() for name in names
               if name not in UNKNOWN_TO_FAERS}
    with open(os.path.join(directory, "lexicon_fixture.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(lexicon, f, indent=2, sort_keys=True)
    print(f"wrote {N_DOCS} documents to {directory}")


def check(directory):
    from services.prescription.schema import result_from_dict
    problems = []
    manifest = json.load(open(os.path.join(directory, "manifest.json"), encoding="utf-8"))
    for d in manifest["documents"]:
        gt_path = os.path.join(directory, d["id"] + ".json")
        if not os.path.exists(os.path.join(directory, d["file"])):
            problems.append(f"{d['id']}: document file missing")
        if not os.path.exists(gt_path):
            problems.append(f"{d['id']}: ground truth missing")
            continue
        gt = json.load(open(gt_path, encoding="utf-8"))
        try:
            result_from_dict({"lines": gt["lines"], "extractor": "local_ocr", "text_source": "ocr"})
        except Exception as exc:
            problems.append(f"{d['id']}: {exc}")
        if not gt.get("synthetic"):
            problems.append(f"{d['id']}: not marked synthetic")
    print("OK: %d documents, each with valid ground truth" % len(manifest["documents"]) if not problems else "\n".join(problems))
    return 1 if problems else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--generate", action="store_true")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    if args.generate:
        generate(HERE)
    if args.check or not args.generate:
        sys.exit(check(HERE))
