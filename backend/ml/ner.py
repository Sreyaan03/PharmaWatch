# backend/ml/ner.py
# Biomedical NER pipeline, loaded on first use.

from transformers import AutoTokenizer, AutoModelForTokenClassification, pipeline
import logging
log = logging.getLogger(__name__)

# ── Biomedical NER model (d4data/biomedical-ner-all) lazy loader ─────────────────────────────────────────────
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

def _load_pipeline():
    global ner_pipeline
    if ner_pipeline is None:
        log.info("Lazy loading Biomedical NER model — first run downloads ~260MB...")
        tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
        model = AutoModelForTokenClassification.from_pretrained(MODEL_NAME)
        ner_pipeline = pipeline("ner", model=model, tokenizer=tokenizer, aggregation_strategy="simple")
        log.info("Biomedical NER model loaded OK")
    return ner_pipeline


def _to_entities(results):
    """Raw pipeline output -> cleaned entities (the same rules for single and batch calls)."""
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


def run_ner(text: str):
    """
    Run the fine-tuned biomedical NER pipeline on text.
    Returns grouped entities with word, label, score, start, end.
    """
    return _to_entities(_load_pipeline()(text))


def run_ner_batch(texts, batch_size=16):
    """run_ner for many texts at once; one entity list per text, same cleaning rules."""
    return [_to_entities(r) for r in _load_pipeline()(list(texts), batch_size=batch_size)]
