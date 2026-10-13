# backend/core/config.py
# Filesystem locations and static configuration shared across the backend.

import os

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT_DIR = os.path.dirname(BACKEND_DIR)
SRC_DIR = os.path.join(ROOT_DIR, "src")
REPO_DATA_DIR = os.path.join(ROOT_DIR, "data")           # small, versioned files (DDI splits)
SPLITS_DIR = os.path.join(REPO_DATA_DIR, "splits")
MODEL_DIR = os.path.join(BACKEND_DIR, "trained_models")
REPORTS_DIR = os.path.join(ROOT_DIR, "reports")

def data_dir():
    """Directory for the large FAERS files and databases.

    Taken from the DATA_DIR environment variable (for example C:/PharmaWatchData, or /app/data in
    a container) and falling back to <repo>/data. Read at call time so a .env loaded
    after import is honoured.
    """
    return os.getenv("DATA_DIR") or REPO_DATA_DIR


# Curated drug-event pairs shown by default in the signal table.
# Events are MedDRA Preferred Terms as they appear in FAERS (checked by test_identity).
WATCHLIST = [
    ("Warfarin", "Subdural haematoma"),
    ("Ciprofloxacin", "Tendon Rupture"),
    ("Metformin", "Lactic Acidosis"),
    ("Amiodarone", "Pulmonary Toxicity"),
    ("Clozapine", "Agranulocytosis"),
    ("Methotrexate", "Hepatotoxicity"),
    ("Simvastatin", "Rhabdomyolysis"),
    ("Isotretinoin", "Depression"),
    ("Lithium", "Nephropathy toxic"),
    ("Valproate", "Hepatotoxicity"),
    ("Haloperidol", "Electrocardiogram QT prolonged"),
    ("Oxycodone", "Respiratory Depression"),
    ("Paclitaxel", "Neuropathy peripheral"),
    ("Amlodipine", "Oedema peripheral"),
    ("Atorvastatin", "Myalgia")
]
