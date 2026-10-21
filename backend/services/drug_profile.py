# backend/services/drug_profile.py
# One page per drug: resolve the name once, then gather every kind of evidence we already hold for
# it by its resolved ingredient. This service computes no statistics of its own — it calls the
# existing services and the FAERS analytics layer and assembles their results.

import logging

log = logging.getLogger("drug_profile")


def _data_version():
    try:
        from faers.registry import data_version
        return data_version()
    except Exception:
        return None


def _surveillance_alerts(rxcui, limit=20):
    """Open surveillance alerts for this ingredient, read straight from the store by rxcui so the
    profile does not resolve the name a second time."""
    try:
        import json

        from db.mysql import query
        rows = query(
            "SELECT kind, pt, quarter, ic025, cases, status, detail_json FROM surveillance_alerts "
            "WHERE rxcui = %s ORDER BY (kind = 'not_in_label') DESC, ic025 DESC LIMIT %s",
            (str(rxcui), int(limit)))
    except Exception:
        return []
    out = []
    for r in rows:
        detail = {}
        try:
            detail = json.loads(r["detail_json"]) if r["detail_json"] else {}
        except Exception:
            detail = {}
        out.append({"kind": r["kind"], "pt": r["pt"], "quarter": r["quarter"], "ic025": r["ic025"],
                    "cases": r["cases"], "status": r["status"], "label": detail.get("label"),
                    "note": detail.get("note")})
    return out


def build_profile(name):
    """Assemble the drug profile. Returns (payload, status). Resolves the name exactly once."""
    from services import signals
    name = (name or "").strip()
    if not name:
        return {"error": "a drug name is required", "resolved": False}, 400

    resolved = signals.resolve_drug(name)          # the one identity resolution for this request
    identity = resolved.get("identity") or {"input": name, "resolved": False, "ingredients": []}
    if not resolved.get("ready"):
        return {"input": name, "resolved": False, "identity": identity,
                "note": "No FAERS data is loaded, so no profile can be built.",
                "data_version": _data_version()}, 200
    if not resolved.get("ok"):
        return {"input": name, "resolved": False, "identity": identity,
                "note": resolved.get("note") or "This drug did not resolve to an indexed ingredient.",
                "data_version": _data_version()}, 200

    ingredient = identity["ingredients"][0]
    rxcui = str(ingredient["rxcui"])
    canonical = ingredient.get("name") or name

    from faers import analytics
    profile = {
        "input": name, "resolved": True, "identity": identity,
        "ingredient": {"rxcui": rxcui, "name": canonical},
        "data_version": _data_version(),
        "report_count": None, "top_reactions": [], "boxed_warning": None,
        "trials": None, "evidence": None, "alerts": [],
        "note": ("A disproportionality signal is a reporting pattern in FAERS, not proof that the "
                 "drug causes the effect. Research use only."),
    }

    try:
        profile["report_count"] = analytics.get_report_count(canonical)
    except Exception as e:
        log.warning(f"[profile] report count failed for {canonical}: {e}")
    try:
        profile["top_reactions"] = analytics.get_top_reactions(canonical, 10) or []
    except Exception as e:
        log.warning(f"[profile] top reactions failed for {canonical}: {e}")

    from services import boxed_warnings
    try:
        profile["boxed_warning"] = boxed_warnings.get_boxed_warning(canonical)[0]
    except Exception as e:
        log.warning(f"[profile] boxed warning failed for {canonical}: {e}")
    try:
        profile["trials"] = boxed_warnings.get_trials(canonical)[0]
    except Exception as e:
        log.warning(f"[profile] trials failed for {canonical}: {e}")

    from services import evidence
    try:
        profile["evidence"] = evidence.links_for_rxcuis([rxcui])[0]
    except Exception as e:
        log.warning(f"[profile] evidence failed for {canonical}: {e}")

    profile["alerts"] = _surveillance_alerts(rxcui)
    return profile, 200
