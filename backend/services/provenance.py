# backend/services/provenance.py
# Which kind of evidence each API path returns, and the data version it was computed from.

import logging
import time

log = logging.getLogger(__name__)

# First matching prefix wins, so the more specific entries come first.
# (prefix, evidence, model_version, data_source)
# data_source 'faers_local' is labelled with the loaded quarters; 'openfda_live' queries the openFDA API.
_EVIDENCE = [
    ("/api/health", "operational", None, None),
    ("/api/system", "operational", None, None),
    ("/api/pipeline/stats", "operational", None, None),
    ("/api/pipeline/alerts", "operational", None, None),
    ("/api/pipeline/pubmed", "retrieved", None, None),
    ("/api/pipeline/trials", "retrieved", None, None),
    ("/api/trials", "retrieved", None, None),
    ("/api/evidence", "retrieved", None, None),
    ("/api/faers/releases", "operational", None, None),
    ("/api/admin/faers", "operational", None, None),
    ("/api/lstm", "modelled", "lstm-forecast", None),
    ("/api/graph", "modelled", "ddi-gnn", None),
    ("/api/interactions", "modelled", "ddi-gnn", None),
    ("/api/ner", "modelled", "biobert-ner", None),
    ("/api/signals/ner-mine", "modelled", "biobert-ner", None),
    ("/api/reddit", "modelled", "biobert-ner", None),
    ("/api/prr", "measured", None, "faers_local"),
    ("/api/signals", "measured", None, "faers_local"),
    ("/api/demographics", "measured", None, "faers_local"),
    ("/api/temporal", "measured", None, "faers_local"),
    ("/api/analytics", "measured", None, "faers_local"),
    ("/api/boxed-warning", "measured", None, "openfda_live"),
    ("/api/dashboard", "measured", None, "faers_local"),
    ("/api/local-stats", "measured", None, "faers_local"),
    ("/api/validation", "measured", None, None),
    ("/api/prescription", "composite", None, None),
]

_VERSION_TTL_SECONDS = 60
_version_cache = {"at": 0.0, "value": None}


def describe(path):
    """(evidence, model_version, data_source) for an API path, or None when the path is not annotated."""
    for prefix, evidence, model, source in _EVIDENCE:
        if path.startswith(prefix):
            return evidence, model, source
    return None


def current_data_version():
    """Label of the loaded FAERS data, memoised briefly so annotating costs no query per request."""
    now = time.monotonic()
    if _version_cache["value"] is not None and now - _version_cache["at"] < _VERSION_TTL_SECONDS:
        return _version_cache["value"]
    try:
        from faers.registry import data_version
        value = data_version()
    except Exception as exc:
        log.warning(f"[PROVENANCE] data version unavailable: {exc}")
        value = "FAERS data not loaded"
    _version_cache.update(at=now, value=value)
    return value
