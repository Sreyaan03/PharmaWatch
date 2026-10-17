# backend/services/prescription/lexicon.py
# Finds drug names in text by matching against names known to the loaded FAERS identity table
# (brand, generic and salt names mapped to RxNorm ingredients). Dictionary matching is far more
# reliable for drug names than the general NER model, which splits names into fragments.

import csv
import difflib
import os
import re

import duckdb

MIN_REPORTS = 20            # a name must appear in this many reports to be trusted as a drug name
MIN_NAME_LENGTH = 4
MAX_WORDS = 4
FUZZY_MIN_LENGTH = 7        # shorter words are too easy to confuse
FUZZY_CUTOFF = 0.88

# Words that are also names in the data (dose forms, nutrients, generic nouns) but in a
# prescription are almost never the drug being prescribed.
STOPWORDS = {
    "tablet", "tablets", "capsule", "capsules", "syrup", "injection", "cream", "ointment", "drops",
    "solution", "suspension", "water", "sodium", "potassium", "calcium", "acid", "oil", "food",
    "with", "after", "before", "daily", "night", "morning", "evening", "dose", "doses", "take",
    "hospital", "patient", "discharge", "medication", "medications", "advice", "follow", "review",
    "once", "twice", "weeks", "weeks", "days", "month", "months", "none", "vitamin", "iron",
    "salt", "sugar", "milk", "saline", "gel", "lotion", "powder", "patch", "spray", "inhaler",
    "diet", "rest", "fluid", "fluids", "normal", "oral", "topical", "stop", "continue", "hold",
}

_WORD = re.compile(r"[A-Za-z][A-Za-z\-']*")
SYNONYMS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "drug_name_synonyms.csv")


def normalize(text):
    return " ".join((text or "").lower().replace("-", " ").split())


class DrugLexicon:
    """name (normalised) -> ingredient display name. Built from the identity table or a dict."""

    def __init__(self, names):
        self.names = {}
        for raw, ingredient in names.items():
            n = normalize(raw)
            if len(n) >= MIN_NAME_LENGTH and n not in STOPWORDS:
                self.names[n] = ingredient
        self._by_first = {}
        for n in self.names:
            self._by_first.setdefault(n[0], []).append(n)

    @classmethod
    def from_database(cls, db_file):
        if not os.path.exists(db_file):
            return cls({})
        con = duckdb.connect(db_file, read_only=True)
        try:
            rows = con.execute(
                "SELECT raw_name, ingredient_name, n_reports FROM drug_identity WHERE n_reports >= ?",
                [MIN_REPORTS]).fetchall()
            names = {}
            for raw, ingredient, _ in rows:
                # a combination product (several ingredients) is not a single drug name
                if "\\" not in (raw or ""):
                    names[raw] = ingredient
                if ingredient:
                    names.setdefault(ingredient, ingredient)
            try:                                # brand names derived from the drug rows (faers.brand_map)
                for brand, ingredient in con.execute("SELECT brand, ingredient_name FROM drug_brand_map").fetchall():
                    names.setdefault(brand, ingredient)
            except duckdb.Error:
                pass                             # database built before the brand map existed
            lex = cls(names)
            lex.add_synonyms()
            return lex
        except duckdb.Error:
            return cls({})
        finally:
            con.close()

    def add_synonyms(self, path=SYNONYMS_PATH):
        """International names (paracetamol, salbutamol...) that map to an ingredient already known."""
        if not os.path.exists(path):
            return
        with open(path, encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                target = self.names.get(normalize(row["ingredient"]))
                name = normalize(row["name"])
                if target and len(name) >= MIN_NAME_LENGTH and name not in self.names:
                    self.names[name] = target
                    self._by_first.setdefault(name[0], []).append(name)

    def __len__(self):
        return len(self.names)

    def find(self, text):
        """Non-overlapping drug mentions, left to right, longest first.

        Each match is {'start', 'end', 'text', 'ingredient', 'match': 'exact'|'fuzzy'}.
        A fuzzy match (one OCR slip away) is reported as such and carries a lower trust.
        """
        words = [(m.start(), m.end(), normalize(m.group())) for m in _WORD.finditer(text)]
        found, i = [], 0
        while i < len(words):
            hit = None
            for span in range(min(MAX_WORDS, len(words) - i), 0, -1):
                chunk = words[i:i + span]
                key = " ".join(w[2] for w in chunk)
                if key in self.names and key not in STOPWORDS:
                    hit = (chunk[0][0], chunk[-1][1], key, self.names[key], "exact", span)
                    break
            if hit is None:
                key = words[i][2]
                if len(key) >= FUZZY_MIN_LENGTH and key not in STOPWORDS:
                    close = difflib.get_close_matches(key, self._by_first.get(key[0], []), n=1, cutoff=FUZZY_CUTOFF)
                    if close:
                        hit = (words[i][0], words[i][1], close[0], self.names[close[0]], "fuzzy", 1)
            if hit:
                start, end, _, ingredient, kind, span = hit
                found.append({"start": start, "end": end, "text": text[start:end],
                              "ingredient": ingredient, "match": kind})
                i += span
            else:
                i += 1
        return found
