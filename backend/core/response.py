# backend/core/response.py
# Provenance attached to analytic responses: how a result was obtained and from which data.

from datetime import datetime, timezone

# measured     counted from reports (FAERS, openFDA, a label) — no model involved
# modelled     produced by a trained or fitted model; a prediction, not an observation
# retrieved    passed through from an external source (literature, trial registry)
# operational  state of this deployment (queues, health), not a pharmacovigilance result
# composite  a report that combines the kinds above; each finding in it states its own kind
EVIDENCE_KINDS = ("measured", "modelled", "retrieved", "operational", "composite")


def with_meta(payload, evidence, data_version=None, model_version=None):
    """Return the payload with a `meta` object added; existing keys are left untouched.

    Only dict payloads are annotated. A payload that already carries `meta` is returned as is.
    """
    if evidence not in EVIDENCE_KINDS:
        raise ValueError(f"unknown evidence kind: {evidence!r}")
    if not isinstance(payload, dict) or "meta" in payload:
        return payload
    out = dict(payload)
    out["meta"] = {
        "evidence": evidence,
        "data_version": data_version,
        "model_version": model_version,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    return out
