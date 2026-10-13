# backend/ml/ocr.py
# EasyOCR reader, loaded on first use.

import torch
import logging
log = logging.getLogger(__name__)

# ── EasyOCR Reader Lazy Loader ────────────────────────────────────────────────
easyocr_reader = None

def get_easyocr_reader():
    global easyocr_reader
    if easyocr_reader is None:
        import easyocr
        log.info("[OCR] Initializing EasyOCR Reader (English)...")
        easyocr_reader = easyocr.Reader(['en'], gpu=torch.cuda.is_available())
    return easyocr_reader
