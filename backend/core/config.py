# backend/core/config.py
# Filesystem locations and static configuration shared across the backend.

import os

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT_DIR = os.path.dirname(BACKEND_DIR)
SRC_DIR = os.path.join(ROOT_DIR, "src")
DATA_DIR = os.path.join(ROOT_DIR, "data")
SPLITS_DIR = os.path.join(DATA_DIR, "splits")
MODEL_DIR = os.path.join(BACKEND_DIR, "trained_models")
REPORTS_DIR = os.path.join(ROOT_DIR, "reports")

# Curated drug-event pairs shown by default in the signal table
WATCHLIST = [
    ("Warfarin", "Bleeding"),
    ("Ciprofloxacin", "Tendon Rupture"),
    ("Metformin", "Lactic Acidosis"),
    ("Amiodarone", "Pulmonary Toxicity"),
    ("Clozapine", "Agranulocytosis"),
    ("Methotrexate", "Hepatotoxicity"),
    ("Simvastatin", "Rhabdomyolysis"),
    ("Isotretinoin", "Depression"),
    ("Lithium", "Nephrotoxicity"),
    ("Valproate", "Hepatotoxicity"),
    ("Haloperidol", "QT Prolongation"),
    ("Oxycodone", "Respiratory Depression"),
    ("Paclitaxel", "Peripheral Neuropathy"),
    ("Amlodipine", "Peripheral Edema"),
    ("Atorvastatin", "Myalgia")
]
