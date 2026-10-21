# backend/services/evidence.py
# External evidence (FDA recalls, PubMed papers, clinical trials) delivered by Kafka and linked to
# drug ingredients by the consumer. This is evidence to read, kept apart from the statistics:
# nothing here changes a signal.

from db.mysql import query
from services.identity import IdentityService

KINDS = ("alert", "paper", "trial")
NOTE = ("External evidence found by name: the item names this drug (and, for papers, this term) "
        "in its title or product text. It is not a statistic and has not been reviewed.")

_identity = None


def _identity_service():
    global _identity
    if _identity is None:
        _identity = IdentityService()
    return _identity


def links_for(drug, pt=None, limit=50):
    """Evidence items for a drug (by name, resolved to ingredients), optionally for one term."""
    resolved = _identity_service().resolve_drug(drug, live=False)
    rxcuis = [str(i["rxcui"]) for i in resolved.get("ingredients", [])]
    if not rxcuis:
        return {"drug": drug, "resolved": False, "ingredients": [], "items": [], "note": NOTE}, 200
    payload, status = links_for_rxcuis(rxcuis, pt=pt, limit=limit)
    if status == 200:
        payload.update({"drug": drug, "resolved": True, "ingredients": resolved["ingredients"]})
    return payload, status


def links_for_rxcuis(rxcuis, pt=None, limit=50):
    """Evidence items for already-resolved ingredient rxcuis (no identity resolution here)."""
    rxcuis = [str(r) for r in rxcuis]
    if not rxcuis:
        return {"items": [], "counts": {k: 0 for k in KINDS}, "note": NOTE}, 200
    marks = ", ".join(["%s"] * len(rxcuis))
    params = list(rxcuis)
    where = f"rxcui IN ({marks})"
    if pt:
        where += " AND pt = %s"
        params.append(pt)
    try:
        rows = query(
            f"SELECT rxcui, ingredient, pt, kind, ref_id, title, url, published, matched_text, linked_at "
            f"FROM evidence_links WHERE {where} ORDER BY linked_at DESC LIMIT %s", tuple(params + [int(limit)]))
    except Exception:
        return {"error": "Evidence store unavailable (MySQL is not reachable).", "items": []}, 503
    items, seen = [], {}
    for r in rows:                       # one item per source document, with every term it names
        key = (r["kind"], r["ref_id"])
        if key not in seen:
            seen[key] = {"kind": r["kind"], "id": r["ref_id"], "title": r["title"], "url": r["url"],
                         "published": r["published"], "ingredient": r["ingredient"], "terms": [],
                         "matched": r["matched_text"], "linked_at": str(r["linked_at"])}
            items.append(seen[key])
        if r["pt"]:
            seen[key]["terms"].append(r["pt"])
    counts = {k: sum(1 for i in items if i["kind"] == k) for k in KINDS}
    return {"pt": pt, "items": items, "counts": counts, "note": NOTE}, 200
