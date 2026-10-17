# backend/services/prescription/review.py
# Composes the existing services into a prescription safety review. It adds no new medical logic:
# interactions come from the interaction service, warnings and classes from the FDA label,
# reactions and signals from FAERS. What it adds is arrangement: duplicates, allergy conflicts,
# ordering, and a summary assembled from the structured findings so that it cannot state anything
# the findings do not contain.
#
# Dose checking is out of scope and the report says so.

import logging
from dataclasses import dataclass
from typing import Callable, Optional

log = logging.getLogger(__name__)

MAX_DRUGS = 8                     # distinct ingredients analysed; every pair is scored (28 at 8)
CATEGORY_ORDER = ["allergy_conflict", "documented_interaction", "boxed_warning", "duplicate_therapy",
                  "model_predicted_interaction", "signal"]
CATEGORY_TITLES = {
    "allergy_conflict": "Listed allergy matches a medication",
    "documented_interaction": "Documented interaction",
    "boxed_warning": "Boxed warning",
    "duplicate_therapy": "Possible duplicate therapy",
    "model_predicted_interaction": "Model-predicted interaction (hypothesis)",
    "signal": "Reporting signals in FAERS",
}
LIMITATIONS = [
    "Doses are not checked: this review does not assess whether a dose is appropriate for the patient.",
    "Findings come from a transcribed medication list that a person confirmed; they do not consider "
    "the patient's conditions, kidney or liver function, age or other medicines not listed.",
    "FAERS signals describe patterns in spontaneous reports; they do not show that a drug caused a reaction.",
    "A model-predicted interaction is a hypothesis from molecular structure, not clinical evidence.",
    "'No evidence found' means no interaction was found in the sources used. It is not a statement "
    "that a combination is acceptable.",
]


@dataclass
class Deps:
    resolve_drug: Callable      # name -> {'resolved': bool, 'ingredients': [{'rxcui', 'name'}]}
    label: Callable             # ingredient name -> {'found', 'pharm_class', 'boxed_warning'}
    profile: Callable           # [rxcui] -> {'exposed_cases', 'reactions', 'signals'} or None
    pairs: Callable             # [names] -> polypharmacy payload {'pairs': [...], 'errors': [...]}


def _finding(category, title, detail, evidence_type, source, **extra):
    return {"category": category, "title": title, "detail": detail,
            "evidence": {"type": evidence_type, "source": source, **extra}}


def _snippet(text, n=320):
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[:n].rsplit(" ", 1)[0] + " ..."


def review(lines, deps, max_drugs=MAX_DRUGS):
    """lines: confirmed medication lines as dicts. Returns the structured review."""
    not_analysed, stopped, allergies = [], [], []
    drugs = {}                                  # ingredient name (lower) -> info
    for idx, line in enumerate(lines):
        text = (line.get("ingredient") or line.get("drug_text") or "").strip()
        status = line.get("status", "active")
        if not text:
            not_analysed.append({"line": idx, "drug_text": line.get("drug_text", ""), "reason": "no drug name"})
            continue
        resolved = deps.resolve_drug(text)
        if not resolved.get("resolved") or not resolved.get("ingredients"):
            not_analysed.append({"line": idx, "drug_text": line.get("drug_text", text),
                                 "reason": "drug name not recognised; correct it or leave it out"})
            continue
        for ing in resolved["ingredients"]:
            key = ing["name"].lower()
            if status == "allergy":
                allergies.append({"line": idx, "ingredient": ing["name"], "rxcui": ing["rxcui"]})
            elif status == "stopped":
                stopped.append({"line": idx, "ingredient": ing["name"]})
            else:
                d = drugs.setdefault(key, {"ingredient": ing["name"], "rxcui": ing["rxcui"], "lines": [],
                                           "drug_texts": []})
                d["lines"].append(idx)
                d["drug_texts"].append(line.get("drug_text", text))

    findings, sections = [], []
    names_all = [d["ingredient"] for d in drugs.values()]
    truncated = names_all[max_drugs:]
    analysed = list(drugs.values())[:max_drugs]
    analysed_names = [d["ingredient"] for d in analysed]

    # allergy conflicts
    for a in allergies:
        if a["ingredient"].lower() in drugs:
            findings.append(_finding(
                "allergy_conflict", f"{a['ingredient']}: listed as an allergy and also as an active medication",
                "The text records an allergy or intolerance to this ingredient, and an active medication "
                "contains it. Check the source document.", "documented", "prescription text"))

    # duplicate therapy: same ingredient on several lines
    for d in analysed:
        if len(d["lines"]) > 1:
            findings.append(_finding(
                "duplicate_therapy", f"{d['ingredient']} appears on {len(d['lines'])} lines",
                f"Listed as {', '.join(sorted(set(t for t in d['drug_texts'])))}. Confirm whether this is "
                "intended or a repeated entry.", "documented", "prescription text"))

    # per-drug sections: label (boxed warning, class), FAERS reactions and signals
    classes = {}
    for d in analysed:
        label = deps.label(d["ingredient"]) or {"found": False}
        profile = deps.profile([d["rxcui"]])
        section = {"ingredient": d["ingredient"], "lines": d["lines"],
                   "label": {"found": bool(label.get("found")), "pharm_class": label.get("pharm_class", [])},
                   "boxed_warning": None, "faers": None}
        if label.get("boxed_warning"):
            section["boxed_warning"] = {"present": True, "text": _snippet(label["boxed_warning"], 1200)}
            findings.append(_finding(
                "boxed_warning", f"{d['ingredient']} has a boxed warning",
                _snippet(label["boxed_warning"]), "label", "FDA label (openFDA)"))
        elif label.get("found"):
            section["boxed_warning"] = {"present": False}
        else:
            section["label"]["note"] = "label data unavailable"
        for c in label.get("pharm_class", []):
            classes.setdefault(c, []).append(d["ingredient"])
        if profile:
            section["faers"] = profile
            strong = [s for s in profile["signals"] if s["signal_strength"] == "strong_signal"][:3]
            if strong:
                findings.append(_finding(
                    "signal", f"{d['ingredient']}: reporting signals in FAERS",
                    "; ".join(f"{s['pt']} ({s['cases']:,} cases, PRR {s['prr']:g})" for s in strong)
                    + ". These are reporting patterns, not proof of causation.",
                    "measured", "FAERS", exposed_cases=profile["exposed_cases"]))
        sections.append(section)

    # duplicate pharmacological class across different ingredients
    for cls, members in classes.items():
        distinct = sorted(set(members))
        if len(distinct) > 1:
            findings.append(_finding(
                "duplicate_therapy", f"{' and '.join(distinct)} share a pharmacological class",
                f"Class: {cls}. Combining members of one class can be intended or a duplication; check the indication.",
                "label", "FDA label (openFDA)"))

    # pairwise interactions via the existing polypharmacy service
    pair_results, pair_errors = [], []
    if len(analysed_names) >= 2:
        payload = deps.pairs(analysed_names)
        pair_results, pair_errors = payload.get("pairs", []), payload.get("errors", [])
    for p in pair_results:
        pair_name = f"{p['drug_a']} + {p['drug_b']}"
        if p["tier"] == "documented":
            findings.append(_finding(
                "documented_interaction", pair_name,
                p.get("label_snippet") or "Documented in an FDA label or interaction database.",
                "label" if p.get("label_documented") else "database",
                p.get("label_found_in") or "interaction database",
                severity=p.get("label_severity"), faers_co_reports=p.get("faers_reports")))
        elif p["tier"] == "model_predicted":
            findings.append(_finding(
                "model_predicted_interaction", pair_name,
                "Predicted from molecular structure only. No documented interaction was found for this pair.",
                "modelled", p.get("model_version") or "interaction model",
                score=p.get("interaction_likelihood_score"), calibrated=p.get("calibrated")))

    order = {c: i for i, c in enumerate(CATEGORY_ORDER)}
    findings.sort(key=lambda f: order[f["category"]])
    for i, f in enumerate(findings, 1):
        f["id"] = i
        f["category_title"] = CATEGORY_TITLES[f["category"]]

    counts = {c: sum(1 for f in findings if f["category"] == c) for c in CATEGORY_ORDER}
    return {
        "analysed": [{"ingredient": d["ingredient"], "lines": d["lines"]} for d in analysed],
        "not_analysed": not_analysed, "stopped": stopped, "allergies": allergies,
        "truncated": truncated, "max_drugs": max_drugs,
        "findings": findings, "counts": counts, "drug_sections": sections,
        "pairs": pair_results, "pair_errors": pair_errors, "limitations": LIMITATIONS,
        "summary": summarise(len(analysed), not_analysed, truncated, counts, len(pair_results)),
    }


def summarise(n_analysed, not_analysed, truncated, counts, n_pairs):
    """Fixed-template summary built only from counts of the structured findings."""
    parts = [f"{n_analysed} medication{'s' if n_analysed != 1 else ''} analysed"
             f"{f' ({n_pairs} pairs checked)' if n_pairs else ''}."]
    labels = [("allergy_conflict", "allergy conflict"), ("documented_interaction", "documented interaction"),
              ("boxed_warning", "boxed warning"), ("duplicate_therapy", "possible duplicate therapy finding"),
              ("model_predicted_interaction", "model-predicted interaction"),
              ("signal", "drug with FAERS reporting signals")]
    found = [f"{counts[k]} {name}{'s' if counts[k] != 1 else ''}" for k, name in labels if counts.get(k)]
    parts.append("Findings: " + ", ".join(found) + "." if found else "No findings in the sources used.")
    if not_analysed:
        parts.append(f"{len(not_analysed)} line{'s' if len(not_analysed) != 1 else ''} could not be analysed.")
    if truncated:
        parts.append(f"{len(truncated)} medication{'s' if len(truncated) != 1 else ''} beyond the limit of "
                     f"{MAX_DRUGS} were not analysed.")
    return " ".join(parts)


def default_deps() -> Optional[Deps]:
    """The production wiring: identity, FDA label, local FAERS and the interaction service."""
    from clients.openfda import fetch_label
    from faers import analytics
    from services import interactions
    from services.dashboard import NON_CLINICAL_EVENT_TERMS
    from services.signals import _identity

    def profile(rxcuis):
        indexed = analytics.indexed_ingredients(rxcuis)
        if indexed is not None and any(r not in indexed for r in rxcuis):
            return None
        return analytics.get_drug_profile(rxcuis, exclude=NON_CLINICAL_EVENT_TERMS)

    def pairs(names):
        body, status = interactions.polypharmacy_analysis({"drugs": names}, max_drugs=MAX_DRUGS)
        return body if status == 200 else {"pairs": [], "errors": [body.get("error", f"HTTP {status}")]}

    return Deps(resolve_drug=lambda name: _identity().resolve_drug(name), label=fetch_label,
                profile=profile, pairs=pairs)
