# backend/services/prescription/parse.py
# Turns text (from a text layer or OCR) into structured medication lines using the drug lexicon
# and rules for strength, dose, route, frequency and duration. It reads what is written; it never
# fills in what is missing, and a name it cannot find in the dictionary is kept as written, flagged.

import re

from services.prescription.lexicon import STOPWORDS, normalize
from services.prescription.schema import MedicationLine

STRENGTH = re.compile(r"(\d+(?:\.\d+)?)\s*(mg|mcg|µg|ug|gm|g|ml|iu|units?|meq|%)(?![a-z])", re.I)
DOSE_AMOUNT = re.compile(
    r"\b(\d+(?:\.\d+)?|half|one|two|three|1/2|½)\s*(tab(?:let)?s?|cap(?:sule)?s?|puffs?|drops?|ml|sachets?|"
    r"teaspoons?|tsp|units?|patch(?:es)?)\b", re.I)
FORMS = [("tablet", r"tab(?:let)?s?\.?"), ("capsule", r"cap(?:sule)?s?\.?"), ("syrup", r"syr(?:up)?\.?|syp\.?"),
         ("injection", r"inj(?:ection)?\.?"), ("drops", r"drops?|gtt"), ("cream", r"cream"),
         ("ointment", r"oint(?:ment)?\.?"), ("gel", r"gel"), ("inhaler", r"inhaler|puffs?"),
         ("patch", r"patch(?:es)?"), ("suspension", r"susp(?:ension)?\.?"), ("sachet", r"sachets?")]
ROUTES = [("oral", r"p\.?o\.?|oral(?:ly)?"), ("intravenous", r"i\.?v\.?|intravenous"),
          ("intramuscular", r"i\.?m\.?|intramuscular"), ("subcutaneous", r"s\.?c\.?|s/c|subcut\w*"),
          ("sublingual", r"s\.?l\.?|sublingual"), ("topical", r"topical(?:ly)?"), ("inhaled", r"inhal\w*|inh"),
          ("rectal", r"rectal(?:ly)?"), ("nasal", r"nasal"), ("ophthalmic", r"ophthalmic|eye")]
AS_NEEDED = re.compile(r"\b(prn|sos|as needed|when required|if needed|if required)\b", re.I)
DURATION = re.compile(r"(?:\bx|×|\bfor|\bfor the next|\bcontinue for)\s*(\d+)\s*(days?|d|weeks?|wks?|w|months?|mo)\b", re.I)
DURATION_BARE = re.compile(r"\b(\d+)\s*(days|weeks|months)\b", re.I)
SCHEDULE = re.compile(r"(?<![\d.])([0-3](?:\.5)?)\s*[-–+]\s*([0-3](?:\.5)?)\s*[-–+]\s*([0-3](?:\.5)?)(?![\d.])")
EVERY_N_HOURS = re.compile(r"\bq\.?\s*(\d{1,2})\s*h(?:rs?|ours?)?\b|\bevery\s+(\d{1,2})\s*hours?\b", re.I)
FREQUENCIES = [
    ("once daily", r"\b(?:o\.?d\.?|q\.?d\.?|once (?:a |per )?day|once daily|daily|every day|1\s*x\s*/?\s*d(?:ay)?)\b"),
    ("twice daily", r"\b(?:b\.?d\.?|b\.?i\.?d\.?|twice (?:a |per )?day|twice daily|2\s*times\s*(?:a|per)\s*day)\b"),
    ("three times daily", r"\b(?:t\.?d\.?s\.?|t\.?i\.?d\.?|thrice|three times (?:a |per )?day|3\s*times\s*(?:a|per)\s*day)\b"),
    ("four times daily", r"\b(?:q\.?i\.?d\.?|q\.?d\.?s\.?|four times (?:a |per )?day|4\s*times\s*(?:a|per)\s*day)\b"),
    ("at bedtime", r"\b(?:h\.?s\.?|q\.?h\.?s\.?|at bed ?time|at night|nocte|night)\b"),
    ("in the morning", r"\b(?:q\.?a\.?m\.?|mane|in the morning|morning)\b"),
    ("once weekly", r"\b(?:once (?:a |per )?week|weekly|q\.?w\.?|1\s*x\s*/?\s*week)\b"),
    ("once (stat)", r"\bstat\b"),
]
STOPPED = re.compile(r"\b(stop(?:ped)?|discontinu\w+|d/c|hold|held|withhold|avoid|do not (?:take|use)|not to (?:take|be taken))\b", re.I)
ALLERGY = re.compile(r"\b(allerg\w*|intoleran\w*|adverse reaction to)\b", re.I)
MARKER = re.compile(r"(?:^|(?<=\s))\d{1,2}[.)]\s+(?=[A-Za-z])")
_NOT_NAMES = STOPWORDS | {"tab", "tabs", "cap", "caps", "inj", "syp", "syr", "susp", "stat", "prn", "sos",
                          "take", "give", "rx", "advice", "name", "date", "age", "sex", "dr", "mrs", "mr", "ms"}


def split_chunks(text):
    """Candidate medication lines: one per text line, further split on list numbers and semicolons."""
    chunks = []
    for line in re.split(r"[\r\n]+", text or ""):
        for part in re.split(r";", line):
            pieces = MARKER.split(part)
            chunks.extend(p.strip(" \t-•*") for p in pieces if p and p.strip(" \t-•*"))
    return [c for c in chunks if len(c) >= 3]


def parse_frequency(segment):
    """(frequency or None, as_needed, note) from one medication segment."""
    note = ""
    as_needed = bool(AS_NEEDED.search(segment))
    m = SCHEDULE.search(segment)
    if m:
        amounts = [float(x) for x in m.groups()]
        times = sum(1 for a in amounts if a > 0)
        label = {1: "once daily", 2: "twice daily", 3: "three times daily"}.get(times)
        if label:
            return label, as_needed, f"schedule {m.group(0)} (morning-noon-night)"
    m = EVERY_N_HOURS.search(segment)
    if m:
        return f"every {m.group(1) or m.group(2)} hours", as_needed, ""
    for label, pattern in FREQUENCIES:
        if re.search(pattern, segment, re.I):
            return label, as_needed, note
    return None, as_needed, note


def parse_route(segment):
    for label, pattern in ROUTES:
        if re.search(r"(?<![\w/])(?:%s)(?![\w/])" % pattern, segment, re.I):
            return label
    return None


def parse_form(context):
    for label, pattern in FORMS:
        if re.search(r"(?<![\w])(?:%s)(?![\w])" % pattern, context, re.I):
            return label
    return None


def parse_duration(segment):
    m = DURATION.search(segment) or DURATION_BARE.search(segment)
    if not m:
        return None
    unit = m.group(2).lower()
    unit = "days" if unit.startswith("d") else "weeks" if unit.startswith("w") else "months"
    return f"{m.group(1)} {unit}"


def _guess_unmatched_name(chunk):
    """A drug-like word that is not in the dictionary: the first plausible word of a dosed line."""
    if not (STRENGTH.search(chunk) and (parse_form(chunk) or parse_frequency(chunk)[0])):
        return None
    for word in re.findall(r"[A-Za-z][A-Za-z\-']{2,}", chunk):
        if normalize(word) not in _NOT_NAMES and not re.fullmatch(r"(?:mg|mcg|ml|iu|bid|tid|qid|prn|sos|bd|tds|od)", word, re.I):
            return word
    return None


def parse_chunk(chunk, lexicon, ocr_confidence=1.0):
    """MedicationLine objects found in one chunk (usually one, occasionally more)."""
    matches = lexicon.find(chunk)
    status = "allergy" if ALLERGY.search(chunk) else "stopped" if STOPPED.search(chunk) else "active"
    lines = []
    if not matches:
        guess = _guess_unmatched_name(chunk)
        if guess is None:
            return lines
        matches = [{"start": chunk.lower().find(guess.lower()), "end": 0, "text": guess,
                    "ingredient": None, "match": None}]
    for i, mt in enumerate(matches):
        end = matches[i + 1]["start"] if i + 1 < len(matches) else len(chunk)
        segment = chunk[mt["start"]:end]
        context = chunk[max(0, mt["start"] - 14):mt["start"]] + " " + segment
        strength = STRENGTH.search(segment)
        amount = DOSE_AMOUNT.search(segment)
        frequency, as_needed, schedule_note = parse_frequency(segment)
        form = parse_form(context)
        route = parse_route(segment) or ("oral" if form in ("tablet", "capsule", "syrup", "suspension", "sachet") and
                                         re.search(r"\bpo\b|oral", chunk, re.I) else None)
        notes = []
        if schedule_note:
            notes.append(schedule_note)
        if mt["match"] == "fuzzy":
            notes.append(f"'{mt['text']}' is close to '{mt['ingredient']}' in the drug dictionary; check the spelling")
        if mt["match"] is None:
            notes.append("name not found in the drug dictionary; check the spelling")
        if status != "active":
            notes.append({"stopped": "text says this medication is stopped, held or to be avoided",
                          "allergy": "text records an allergy or intolerance, not a medication being taken"}[status])
        name_conf = {"exact": 0.95, "fuzzy": 0.6, None: 0.3}[mt["match"]]
        completeness = sum(bool(x) for x in (strength, frequency, route or form)) / 3.0
        confidence = 0.55 * name_conf + 0.25 * completeness + 0.20 * min(max(ocr_confidence, 0.0), 1.0)
        # a line is never more trustworthy than its drug name, however complete the dosing is
        confidence = round(min(confidence, {"exact": 1.0, "fuzzy": 0.8, None: 0.5}[mt["match"]]), 2)
        lines.append(MedicationLine(
            drug_text=mt["text"], strength=strength.group(0).strip() if strength else None,
            dose_form=form, dose=amount.group(0).strip() if amount else None, route=route,
            frequency=frequency, duration=parse_duration(segment), as_needed=as_needed,
            confidence=confidence, notes="; ".join(notes), status=status,
            ingredient=mt["ingredient"], match=mt["match"]))
    return lines


def parse_medications(text, lexicon, line_confidences=None):
    """Parse text into MedicationLines. line_confidences: optional {chunk text: OCR confidence 0..1}."""
    out = []
    for chunk in split_chunks(text):
        conf = (line_confidences or {}).get(chunk, 1.0)
        out.extend(parse_chunk(chunk, lexicon, conf))
    return out
