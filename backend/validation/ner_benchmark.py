"""
NER benchmark: how well does the deployed model find drugs and adverse effects in text?

    python -m validation.ner_benchmark            # all sentences
    python -m validation.ner_benchmark --smoke    # fixed sample of 200 sentences

Corpus: ADE Corpus V2, drug-adverse-effect relation annotations (Gurulingappa et al., J Biomed
Inform 2012;45(5):885-92), read from a local parquet file (see DEFAULT_DATA). The licence is not
stated on the dataset card, so the data is not committed to the repository.

The deployed pipeline is scored as the application uses it (ml.ner.run_ner_batch):
  drug    = entity label Medication or Drug
  effect  = entity label Disease_disorder or Sign_symptom
Matching is reported two ways: exact (same character span) and overlap (any shared characters,
one gold span matched to at most one prediction).

Limitation: ADE annotates only the drugs and effects that take part in an annotated relation.
Correct predictions of other drugs or symptoms in the same sentence count as false positives, so
precision here is a lower bound; recall is unaffected.

Pairing: the application pairs every drug with every effect in a text. That all-pairs rule is
compared with sentence-level pairing on 3-sentence passages (fixed seed), against the annotated
relations, so a later change to the rule has a baseline.
"""

import argparse
import hashlib
import json
import os
import random
import re
import sys
from datetime import datetime, timezone

import pandas as pd

from ml.ner import MODEL_NAME

REPORTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                           "reports", "validation")
DEFAULT_DATA = os.path.join(os.environ.get("DATA_DIR", r"C:\PharmaWatchData"), "reference",
                            "ade_drug_ade_relation.parquet")
DRUG_LABELS = ("Medication", "Drug")
EFFECT_LABELS = ("Disease_disorder", "Sign_symptom")
SMOKE_SENTENCES = 200
PASSAGE_SENTENCES = 3
SEED = 17


# ── gold data ────────────────────────────────────────────────────────────────
def load_gold(path):
    """{sentence: {'drugs': {(start, end)}, 'effects': {...}, 'pairs': {(drug_text, effect_text)}}}."""
    df = pd.read_parquet(path)
    gold = {}
    for row in df.itertuples(index=False):
        g = gold.setdefault(row.text, {"drugs": set(), "effects": set(), "pairs": set()})
        for s, e in zip(row.indexes["drug"]["start_char"], row.indexes["drug"]["end_char"]):
            g["drugs"].add((int(s), int(e)))
        for s, e in zip(row.indexes["effect"]["start_char"], row.indexes["effect"]["end_char"]):
            g["effects"].add((int(s), int(e)))
        g["pairs"].add((row.drug.strip().lower(), row.effect.strip().lower()))
    return gold


def file_sha256(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


# ── span matching and scores ─────────────────────────────────────────────────
def match_spans(gold, pred, mode):
    """Number of true positives between two span sets; each span is used at most once."""
    gold, pred = sorted(gold), sorted(pred)
    if mode == "exact":
        return len(set(gold) & set(pred))
    used, tp = set(), 0
    for g in gold:
        for i, p in enumerate(pred):
            if i not in used and g[0] < p[1] and p[0] < g[1]:
                used.add(i)
                tp += 1
                break
    return tp


def prf(tp, n_pred, n_gold):
    p = tp / n_pred if n_pred else 0.0
    r = tp / n_gold if n_gold else 0.0
    f = 2 * p * r / (p + r) if (p + r) else 0.0
    return {"precision": round(p, 4), "recall": round(r, 4), "f1": round(f, 4),
            "tp": tp, "predicted": n_pred, "gold": n_gold}


def count_fragments(gold, pred):
    """Predicted spans that lie strictly inside a gold span: pieces of an entity, not the entity."""
    return sum(1 for p in pred if any(g[0] <= p[0] and p[1] <= g[1] and p != g for g in gold))


def score_entities(gold, predictions):
    """Per entity type and matching mode. predictions: {sentence: {'drugs': set, 'effects': set}}.

    Also reports `fragment_share`: the share of predictions that are only a piece of a gold entity.
    Overlap matching counts such a piece as correct; downstream use of it as a name would not be.
    """
    out = {}
    for kind in ("drugs", "effects"):
        out[kind] = {}
        for mode in ("exact", "overlap"):
            tp = n_pred = n_gold = 0
            for sentence, g in gold.items():
                p = predictions[sentence][kind]
                tp += match_spans(g[kind], p, mode)
                n_pred += len(p)
                n_gold += len(g[kind])
            out[kind][mode] = prf(tp, n_pred, n_gold)
        frag = sum(count_fragments(g[kind], predictions[s][kind]) for s, g in gold.items())
        total = sum(len(predictions[s][kind]) for s in gold)
        out[kind]["fragment_share"] = round(frag / total, 4) if total else 0.0
        out[kind]["fragments"] = frag
    return out


# ── pairing ──────────────────────────────────────────────────────────────────
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def split_sentences(text):
    """(sentence, offset) pieces of a passage, split on sentence-final punctuation."""
    pieces, pos = [], 0
    for part in _SENTENCE_END.split(text):
        start = text.index(part, pos)
        pieces.append((part, start))
        pos = start + len(part)
    return pieces


def pairs_all(entities):
    """The application's rule: every drug with every effect in the text."""
    drugs = {e["token"].lower() for e in entities if e["label"] in DRUG_LABELS}
    effects = {e["token"].lower() for e in entities if e["label"] in EFFECT_LABELS}
    return {(d, f) for d in drugs for f in effects}


def pairs_sentence(entities, text):
    """Drug-effect pairs that share a sentence."""
    out = set()
    for sentence, offset in split_sentences(text):
        lo, hi = offset, offset + len(sentence)
        inside = [e for e in entities if lo <= e["start"] < hi]
        out |= pairs_all(inside)
    return out


def score_pairs(gold_pairs, predicted):
    tp = len(gold_pairs & predicted)
    return prf(tp, len(predicted), len(gold_pairs))


def make_passages(sentences, k=PASSAGE_SENTENCES, seed=SEED):
    rng = random.Random(seed)
    order = list(sentences)
    rng.shuffle(order)
    return [order[i:i + k] for i in range(0, len(order) - k + 1, k)]


# ── the run ──────────────────────────────────────────────────────────────────
def run(path, smoke=False, out_path=None):
    from ml.ner import run_ner_batch
    gold = load_gold(path)
    sentences = sorted(gold)
    if smoke:
        sentences = random.Random(SEED).sample(sentences, min(SMOKE_SENTENCES, len(sentences)))
        sentences.sort()
        gold = {s: gold[s] for s in sentences}

    ents = dict(zip(sentences, run_ner_batch(sentences)))
    predictions = {s: {"drugs": {(e["start"], e["end"]) for e in ents[s] if e["label"] in DRUG_LABELS},
                       "effects": {(e["start"], e["end"]) for e in ents[s] if e["label"] in EFFECT_LABELS}}
                   for s in sentences}
    entity_scores = score_entities(gold, predictions)

    passages = make_passages(sentences)
    texts = [" ".join(p) for p in passages]
    passage_ents = run_ner_batch(texts)
    all_tp = sent_tp = n_all = n_sent = n_gold = 0
    for passage, text, entities in zip(passages, texts, passage_ents):
        gp = set().union(*[gold[s]["pairs"] for s in passage])
        pa, ps = pairs_all(entities), pairs_sentence(entities, text)
        all_tp += len(gp & pa)
        sent_tp += len(gp & ps)
        n_all += len(pa)
        n_sent += len(ps)
        n_gold += len(gp)
    pairing = {"passages": len(passages), "sentences_per_passage": PASSAGE_SENTENCES, "seed": SEED,
               "matching": "lowercase text of the drug and of the effect",
               "all_pairs_rule (current)": prf(all_tp, n_all, n_gold),
               "sentence_level_rule": prf(sent_tp, n_sent, n_gold)}

    report = {
        "benchmark": "ner",
        "run_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "smoke": smoke,
        "model": MODEL_NAME,
        "label_mapping": {"drug": list(DRUG_LABELS), "effect": list(EFFECT_LABELS)},
        "dataset": {"name": "ADE Corpus V2, drug-adverse-effect relations", "file": os.path.basename(path),
                    "sha256": file_sha256(path), "sentences_scored": len(sentences),
                    "source": "https://huggingface.co/datasets/ade-benchmark-corpus/ade_corpus_v2",
                    "citation": "Gurulingappa H et al. J Biomed Inform 2012;45(5):885-92",
                    "licence": "not stated on the dataset card; data not committed"},
        "matching": {"exact": "same character span", "overlap": "any shared characters, one-to-one"},
        "entities": entity_scores,
        "pairing": pairing,
        "limitations": ["ADE annotates only entities that take part in a relation, so precision is a lower bound.",
                        "Scores apply to this corpus (case reports and abstracts), not to prescriptions or social media."],
    }
    out_path = out_path or os.path.join(REPORTS_DIR, "ner_smoke.json" if smoke else "ner.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(report, f, indent=2, sort_keys=True)
        f.write("\n")
    return report, out_path


def main(argv=None):
    parser = argparse.ArgumentParser(description="Score the deployed NER model on the ADE corpus.")
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--data", default=DEFAULT_DATA)
    args = parser.parse_args(argv)
    if not os.path.exists(args.data):
        print(f"[-] ADE corpus not found at {args.data}. See the module docstring for where it comes from.",
              file=sys.stderr)
        return 2
    report, path = run(args.data, smoke=args.smoke)
    for kind, modes in report["entities"].items():
        print(kind, {m: (v["precision"], v["recall"], v["f1"]) for m, v in modes.items() if isinstance(v, dict)},
              "fragment share", modes["fragment_share"])
    print("pairing:", {k: v for k, v in report["pairing"].items() if isinstance(v, dict)})
    print("wrote", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
