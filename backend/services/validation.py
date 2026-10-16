# backend/services/validation.py
# Serves the recorded benchmark results (reports/validation/summary.json) for the
# Methods & Validation page. It computes nothing: the page shows what the last run wrote.

import json
import os

SUMMARY_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                            "reports", "validation", "summary.json")


def validation_summary():
    """The last recorded validation summary, or an explicit 'not run' payload."""
    if not os.path.exists(SUMMARY_PATH):
        return {"components": {}, "not_run": ["signals", "ddi", "ner"], "known_limitations": [],
                "message": "No validation run is recorded. Run: python -m validation.run_all"}, 200
    try:
        with open(SUMMARY_PATH, encoding="utf-8") as f:
            return json.load(f), 200
    except (OSError, ValueError) as exc:
        return {"error": f"validation summary unreadable: {exc}"}, 500
