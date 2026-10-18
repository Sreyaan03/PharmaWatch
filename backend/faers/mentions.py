# backend/faers/mentions.py
# Finds drug ingredients and MedDRA Preferred Terms named in short texts (paper titles, trial
# titles, recall descriptions), so external evidence can be attached to the drug it concerns.
#
# Names come from the identity tables built by faers.identity_build: drug names seen in at least
# MIN_DRUG_REPORTS reports and resolved to an ingredient, the ingredient names themselves, and the
# brand map. Matching is exact on whole words, longest name first; nothing is guessed. A name that
# is not in the tables is not found, and that is the intended failure.

import os
import re

import duckdb

MIN_DRUG_REPORTS = 20
MIN_NAME_LENGTH = 4
MIN_PT_LENGTH = 6
MIN_PT_REPORTS = 50
MAX_WORDS = 5

# Words that occur as drug names in FAERS but are ordinary words in a title.
NOT_DRUG_NAMES = {
    "unknown", "placebo", "other", "drug", "drugs", "product", "products", "tablet", "tablets",
    "capsule", "capsules", "medication", "medications", "study", "control", "treatment", "therapy",
    "patients", "patient", "human", "safety", "adverse", "event", "events", "health", "food",
    "test", "trial", "standard", "combination", "contrast", "vaccine", "vaccines", "water",
    "oxygen", "alcohol", "nicotine", "caffeine", "sugar", "salt", "fluoride", "iron", "zinc",
}

# Preferred Terms too general to mean anything when found in a title.
GENERIC_PTS = {
    "death", "drug ineffective", "off label use", "product use issue", "no adverse event",
    "intentional product misuse", "drug interaction", "therapeutic response decreased",
    "condition aggravated", "general physical health deterioration", "inappropriate schedule of product administration",
    "product use in unapproved indication", "drug ineffective for unapproved indication", "malaise",
    "treatment failure", "hospitalisation", "therapy non-responder", "therapy interrupted",
}

_WORD = re.compile(r"[a-z0-9]+(?:'[a-z0-9]+)*")       # hyphens split: "metformin-associated"


def _words(text):
    return _WORD.findall((text or "").lower())


def _key(name):
    return " ".join(_words(name))


class MentionIndex:
    """drugs: {name: (ingredient_rxcui, ingredient_name)}; pts: {lower-case term: Preferred Term}."""

    def __init__(self, drugs=None, pts=None):
        self.drugs = {}
        for name, value in (drugs or {}).items():
            key = _key(name)
            if len(key) >= MIN_NAME_LENGTH and key not in NOT_DRUG_NAMES and len(key.split()) <= MAX_WORDS:
                self.drugs.setdefault(key, value)
        self.pts = {}
        for low, pt in (pts or {}).items():
            key = _key(low)
            if len(key) >= MIN_PT_LENGTH and key not in GENERIC_PTS and len(key.split()) <= MAX_WORDS:
                self.pts.setdefault(key, pt)

    def __len__(self):
        return len(self.drugs)

    @classmethod
    def from_database(cls, db_file):
        if not db_file or not os.path.exists(db_file):
            return cls()
        con = duckdb.connect(db_file, read_only=True)
        try:
            drugs = {}
            rows = con.execute(
                "SELECT raw_name, ingredient_rxcui, ingredient_name FROM drug_identity "
                "WHERE ingredient_rxcui IS NOT NULL AND n_reports >= ? AND raw_name NOT LIKE '%\\%'",
                [MIN_DRUG_REPORTS]).fetchall()
            for raw, rxcui, ingredient in rows:
                drugs.setdefault(raw, (str(rxcui), ingredient))
                drugs.setdefault(ingredient, (str(rxcui), ingredient))
            try:
                for brand, rxcui, ingredient in con.execute(
                        "SELECT brand, ingredient_rxcui, ingredient_name FROM drug_brand_map").fetchall():
                    drugs.setdefault(brand, (str(rxcui), ingredient))
            except duckdb.Error:
                pass                                   # database built before the brand map existed
            pts = {}
            try:
                for pt, low in con.execute("SELECT pt, pt_lower FROM pt_dictionary WHERE n_reports >= ?",
                                           [MIN_PT_REPORTS]).fetchall():
                    pts[low] = pt
            except duckdb.Error:
                pass
            return cls(drugs, pts)
        except duckdb.Error:
            return cls()
        finally:
            con.close()

    def _scan(self, text, table):
        words = _words(text)
        found, i = [], 0
        while i < len(words):
            for n in range(min(MAX_WORDS, len(words) - i), 0, -1):
                key = " ".join(words[i:i + n])
                if key in table:
                    found.append((key, table[key]))
                    i += n
                    break
            else:
                i += 1
        return found

    def drugs_in(self, text):
        """[{rxcui, ingredient, matched}] for each ingredient named in the text, first mention first."""
        out, seen = [], set()
        for key, (rxcui, ingredient) in self._scan(text, self.drugs):
            if rxcui not in seen:
                seen.add(rxcui)
                out.append({"rxcui": rxcui, "ingredient": ingredient, "matched": key})
        return out

    def pts_in(self, text):
        """[{pt, matched}] for each Preferred Term named in the text."""
        out, seen = [], set()
        for key, pt in self._scan(text, self.pts):
            if pt not in seen:
                seen.add(pt)
                out.append({"pt": pt, "matched": key})
        return out
