# 09-01 Summary: extraction and durable jobs

- Extractor interface with a local extractor (PDF text layer, else EasyOCR at 200 dpi) and an optional cloud vision extractor (off unless a key is set).
- Rules parser: strength, dose form, route, frequency (including 1-0-1), duration, PRN, stopped and allergy lines. Drug names matched against a FAERS-derived dictionary; unmatched names are flagged, never guessed.
- MySQL `jobs` table and `core/jobs.py` replace the in-memory task dict; NER mining moved onto it. 503 when the job store is down; interrupted jobs recovered at startup.
