# backend/helpers/models.py
# ─────────────────────────────────────────────────────────────────────────────
# Lazy-loaders and shared configurations for GNN, BioBERT, and OCR models
# ─────────────────────────────────────────────────────────────────────────────

import os
import sys
import torch
from transformers import AutoTokenizer, AutoModelForTokenClassification, pipeline

# ── Paths ────────────────────────────────────────────────────────────────────
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL_PATH = os.path.join(BACKEND_DIR, "trained_models", "drugbank_gine_model.pth")
CALIBRATION_PATH = os.path.join(BACKEND_DIR, "trained_models", "model_calibration.json")
SCRIPTS_DIR = os.path.join(BACKEND_DIR, "..", "scripts")

# Insert scripts folder to sys.path dynamically for DDI model imports
if os.path.abspath(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, os.path.abspath(SCRIPTS_DIR))

# ── EasyOCR Reader Lazy Loader ────────────────────────────────────────────────
easyocr_reader = None

def get_easyocr_reader():
    global easyocr_reader
    if easyocr_reader is None:
        import easyocr
        print("[OCR] Initializing EasyOCR Reader (English)...")
        easyocr_reader = easyocr.Reader(['en'], gpu=torch.cuda.is_available())
    return easyocr_reader

# ── BioBERT NER Model Lazy Loader ─────────────────────────────────────────────
MODEL_NAME = "d4data/biomedical-ner-all"
ner_pipeline = None

COMMON_STOPWORDS = {
    "the", "and", "a", "of", "to", "in", "is", "that", "it", "on", "for", "with", "as", 
    "at", "by", "an", "be", "this", "are", "from", "or", "had", "have", "but", "not", "was",
    "were", "she", "he", "his", "her", "they", "them", "their", "we", "us", "our", "you", 
    "your", "me", "my", "i", "can", "will", "would", "should", "could", "about", "which",
    "who", "whom", "whose", "has", "been", "being", "do", "does", "did", "done", "doing",
    "some", "any", "no", "yes", "all", "any", "both", "each", "few", "more", "most", "other",
    "some", "such", "than", "too", "very", "s", "t", "just", "now", "d", "ll", "m", "o", "re",
    "ve", "y", "ain", "aren", "couldn", "didn", "doesn", "hadn", "hasn", "haven", "isn", "ma",
    "mightn", "mustn", "needn", "shan", "shouldn", "wasn", "weren", "won", "wouldn", "li", "la",
    "le", "lo", "el", "patient", "patients", "history", "admitted", "discharged", "hospital",
    "clinical", "medical", "treatment", "therapy", "report", "reports", "findings", "symptoms"
}

def clean_and_validate_entity(token: str) -> str:
    """Cleans OCR/NER token, returns cleaned string if valid, else empty string."""
    if not token:
        return ""
    cleaned = token.strip()
    cleaned = cleaned.strip(".,:-_/\\()[]{}'\"`*#@&")
    cleaned = cleaned.strip()
    if len(cleaned) < 3:
        return ""
    if not any(c.isalpha() for c in cleaned):
        return ""
    if cleaned.lower() in COMMON_STOPWORDS:
        return ""
    return cleaned

def run_ner(text: str):
    """
    Run the fine-tuned biomedical NER pipeline on text.
    Returns grouped entities with word, label, score, start, end.
    """
    global ner_pipeline
    if ner_pipeline is None:
        print("Lazy loading Biomedical NER model — first run downloads ~260MB...")
        tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
        model = AutoModelForTokenClassification.from_pretrained(MODEL_NAME)
        ner_pipeline = pipeline("ner", model=model, tokenizer=tokenizer, aggregation_strategy="simple")
        print("Biomedical NER model loaded OK")

    results = ner_pipeline(text)
    entities = []
    for ent in results:
        cleaned_word = clean_and_validate_entity(ent["word"])
        if cleaned_word:
            entities.append({
                "token": cleaned_word,
                "label": ent["entity_group"],
                "score": round(float(ent["score"]), 4),
                "start": ent["start"],
                "end": ent["end"]
            })
    return entities

# ── GNN Model Lazy Loader & Temperature Scaling ───────────────────────────────
_gnn_model       = None
_gnn_loaded      = False
_gnn_temperature = 1.0

def get_gnn_temperature() -> float:
    global _gnn_temperature
    if os.path.exists(CALIBRATION_PATH):
        try:
            import json as _json
            with open(CALIBRATION_PATH, 'r') as f:
                data = _json.load(f)
                _gnn_temperature = float(data.get("optimal_temperature", 1.0))
        except Exception:
            _gnn_temperature = 1.0
    return _gnn_temperature

def _load_gnn():
    global _gnn_model, _gnn_loaded
    if _gnn_loaded:
        return _gnn_model
    try:
        from models_ddi import GINEDDIPredictor
        import torch as _torch
        # Architecture: GINEConv x4, hidden_dim=128, multi-pooling, Morgan FP branch
        m = GINEDDIPredictor(
            node_in_dim=22,
            edge_in_dim=10,
            hidden_dim=128,
            num_layers=4,
            dropout=0.1,
            use_fingerprint=True
        )
        if os.path.exists(MODEL_PATH):
            m.load_state_dict(_torch.load(MODEL_PATH, map_location="cpu"))
            m.eval()
            _gnn_model = m
            print("[GNN] GINEDDIPredictor model loaded OK from drugbank_gine_model.pth")
        else:
            print(f"[GNN] Model checkpoint not found at {MODEL_PATH}")
        _gnn_loaded = True
    except Exception as e:
        print(f"[GNN] Could not load model: {e}")
        _gnn_loaded = True
    return _gnn_model

# ── Common Config / Watchlist ────────────────────────────────────────────────
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

# Background tasks dictionary for streaming NER
MINING_TASKS = {}
