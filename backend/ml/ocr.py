# backend/ml/ocr.py
# EasyOCR reader, loaded on first use.

import torch

# ── EasyOCR Reader Lazy Loader ────────────────────────────────────────────────
easyocr_reader = None

def get_easyocr_reader():
    global easyocr_reader
    if easyocr_reader is None:
        import easyocr
        print("[OCR] Initializing EasyOCR Reader (English)...")
        easyocr_reader = easyocr.Reader(['en'], gpu=torch.cuda.is_available())
    return easyocr_reader
