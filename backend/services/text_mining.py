# backend/services/text_mining.py
# Turn NER entities from a clinical narrative into drug-event MENTIONS, not signals.
#
# A mention is formed only when a drug and an event occur in the SAME sentence, and the event is
# not under a negation cue. Mentions are resolved through the identity service; only resolved pairs
# are looked up against FAERS (to show the existing disproportionality for that pair), and a mention
# is stored with source `text_mining`, kept separate from signals. A mention never enters a 2x2
# count: the 2x2 always comes from FAERS itself.

import re

# Short, documented negation cue list. An event is dropped when one of these appears earlier in the
# same sentence than the event (e.g. "No bleeding was observed", "denies chest pain",
# "without nausea", "ruled out MI", "negative for rash").
NEGATION_CUES = ["no", "not", "denies", "denied", "without", "ruled out", "negative for",
                 "absence of", "free of", "no evidence of"]

DRUG_LABELS = ("Medication", "Drug")
# Adverse-event labels from d4data/biomedical-ner-all. "Outcome" and "Clinical_event" are included
# so serious outcomes such as death are captured (e.g. "died" is tagged Outcome).
EVENT_LABELS = ("Disease_disorder", "Sign_symptom", "Clinical_event", "Outcome")

_SENT_SPLIT = re.compile(r"[.;\n]+")


def _sentences(text):
    """Yield (start, end, sentence_text) spans covering the text."""
    spans, pos = [], 0
    for m in _SENT_SPLIT.finditer(text):
        spans.append((pos, m.start(), text[pos:m.start()]))
        pos = m.end()
    if pos < len(text):
        spans.append((pos, len(text), text[pos:]))
    return spans


def _is_negated(sentence_lower, event_offset_in_sentence):
    """True if a negation cue appears before the event within the sentence."""
    before = sentence_lower[:event_offset_in_sentence]
    for cue in NEGATION_CUES:
        # word-boundary match so "nor" does not match "no", etc.
        if re.search(r"(?<![a-z])" + re.escape(cue) + r"(?![a-z])", before):
            return True
    return False


def pair_mentions(text, entities):
    """Drug-event mentions formed within one sentence, dropping negated events. Pure: no model, no
    DB. Returns a list of {drug, event, confidence, sentence} ordered by confidence."""
    drugs = [e for e in entities if e.get("label") in DRUG_LABELS and e.get("start") is not None]
    events = [e for e in entities if e.get("label") in EVENT_LABELS and e.get("start") is not None]
    pairs, seen = [], set()
    for s_start, s_end, s_text in _sentences(text):
        s_lower = s_text.lower()
        s_drugs = [d for d in drugs if s_start <= d["start"] < s_end]
        s_events = [ev for ev in events if s_start <= ev["start"] < s_end]
        if not s_drugs or not s_events:
            continue
        for ev in s_events:
            if _is_negated(s_lower, ev["start"] - s_start):
                continue
            for d in s_drugs:
                key = (d["token"].lower(), ev["token"].lower())
                if key in seen:
                    continue
                seen.add(key)
                pairs.append({"drug": d["token"], "event": ev["token"],
                              "confidence": round(float(d["score"]) * float(ev["score"]), 4),
                              "sentence": s_text.strip()})
    pairs.sort(key=lambda x: x["confidence"], reverse=True)
    return pairs


def resolve_and_score(pairs, limit=5):
    """Resolve each mention through the identity service; score only resolved pairs against FAERS
    (the existing disproportionality for that pair). Returns (mentions, unresolved).

    A mention carries its FAERS score for display but is never written as a signal and never enters
    a 2x2 count. Unresolved mentions are listed as such, not scored.
    """
    from services.identity import IdentityService
    from services.signals import compute_disproportionality_helper
    ident = IdentityService()

    mentions, unresolved = [], []
    for p in pairs[:limit]:
        drug_res = ident.resolve_drug(p["drug"], live=False)
        event_res = ident.resolve_event(p["event"])
        drug_ok = bool(drug_res.get("resolved") and drug_res.get("ingredients"))
        event_ok = bool((event_res or {}).get("resolved") or (event_res or {}).get("pt"))
        if not (drug_ok and event_ok):
            unresolved.append({"drug": p["drug"], "event": p["event"], "sentence": p.get("sentence"),
                               "drug_resolved": drug_ok, "event_resolved": event_ok})
            continue
        faers = None
        try:
            faers = compute_disproportionality_helper(p["drug"], p["event"])
        except Exception:
            faers = None
        mentions.append({
            "drug": p["drug"], "event": p["event"], "confidence": p["confidence"],
            "sentence": p.get("sentence"), "source": "text_mining",
            "faers": {"prr": faers.get("prr"), "signal_strength": faers.get("signal_strength"),
                      "a": faers.get("a"), "ror_ci_low": faers.get("ror_ci_low"),
                      "ic025": faers.get("ic025")} if faers else None,
        })
    return mentions, unresolved
