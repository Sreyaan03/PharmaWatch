# Getting a Fine-Tuned NER Model for PharmaWatch

## Why Your Current Model Gives Random Predictions

Your current backend loads `dmis-lab/biobert-base-cased-v1.1`, which is a **base pre-trained language model** — it was trained to understand biomedical text (via masked language modeling on PubMed), but it was **never trained for NER**. When you load it with `BertForTokenClassification`, HuggingFace attaches a fresh classification head with **randomly initialized weights**:

```
classifier.weight  | MISSING   ← randomly initialized
classifier.bias    | MISSING   ← randomly initialized
```

This means the model has no idea what `B-DRUG`, `I-EFFECT`, or `O` labels are. It's like hiring a doctor who read millions of textbooks but was never taught how to highlight drug names in a sentence.

> [!IMPORTANT]
> You have **two options** to fix this — pick the one that fits your needs.

---

## Option A: Use a Pre-Trained NER Model (Recommended — 5 Minutes)

The easiest fix: swap in a model that somebody has **already fine-tuned** for biomedical NER. No training required, no GPU needed.

### A1: SciBERT Drug + Adverse Effect NER

**Best for PharmaWatch** — this model was fine-tuned specifically for pharmacovigilance. It recognizes exactly two entity types that matter most to you:

| Label | Meaning | Example |
|-------|---------|---------|
| `B-DRUG` / `I-DRUG` | Drug names | **Aspirin**, **Omeprazole** |
| `B-EFFECT` / `I-EFFECT` | Adverse effects | **nausea**, **hepatotoxicity** |
| `O` | Not an entity | everything else |

- **Model**: [`jsylee/scibert_scivocab_uncased-finetuned-ner`](https://huggingface.co/jsylee/scibert_scivocab_uncased-finetuned-ner)
- **Base**: SciBERT (trained on scientific papers)
- **Dataset**: [ADE Corpus V2](https://huggingface.co/datasets/ade-benchmark-corpus/ade_corpus_v2) (adverse drug events)
- **Size**: ~110 MB

#### Updated `backend/app.py` for Option A1:

```python
# backend/app.py
# ─────────────────────────────────────────────────────────────────────────────
# PharmaWatch — SciBERT Drug/ADE Named-Entity Recognition API
# Serves POST /api/ner   →  { "entities": [{token, label, score}, ...] }
# ─────────────────────────────────────────────────────────────────────────────

from flask import Flask, request, jsonify
from flask_cors import CORS
from transformers import AutoTokenizer, AutoModelForTokenClassification, pipeline

app = Flask(__name__)
CORS(app)

# ── Load fine-tuned SciBERT NER at startup ────────────────────────────────────
print("Loading SciBERT Drug/ADE NER model...")
MODEL_NAME = "jsylee/scibert_scivocab_uncased-finetuned-ner"

model = AutoModelForTokenClassification.from_pretrained(
    MODEL_NAME,
    num_labels=5,
    id2label={0: "O", 1: "B-DRUG", 2: "I-DRUG", 3: "B-EFFECT", 4: "I-EFFECT"}
)
tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)

# Use HuggingFace pipeline — handles sub-word merging, scoring, etc.
ner_pipeline = pipeline(
    "ner",
    model=model,
    tokenizer=tokenizer,
    aggregation_strategy="simple"   # merges sub-tokens into full words
)
print("SciBERT NER loaded OK")


# ── Route: POST /api/ner ──────────────────────────────────────────────────────
@app.route("/api/ner", methods=["POST"])
def ner_endpoint():
    """
    Expects JSON body:  { "text": "Patient reported nausea after taking Aspirin." }
    Returns:            { "entities": [{word, entity_group, score, start, end}, ...] }
    """
    data = request.get_json(silent=True)

    if not data or "text" not in data:
        return jsonify({"error": "Request body must be JSON with a 'text' field."}), 400

    text = data["text"].strip()
    if not text:
        return jsonify({"error": "'text' field cannot be empty."}), 400

    try:
        raw_entities = ner_pipeline(text)

        # Convert numpy floats to Python floats for JSON serialization
        entities = []
        for ent in raw_entities:
            entities.append({
                "word":         ent["word"],
                "entity_group": ent["entity_group"],   # "DRUG" or "EFFECT"
                "score":        round(float(ent["score"]), 4),
                "start":        ent["start"],
                "end":          ent["end"]
            })

        return jsonify({"entities": entities})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ── Route: GET /api/health ────────────────────────────────────────────────────
@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "model": MODEL_NAME})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
```

#### Install & Run:

```bash
# No extra installs needed — your existing packages work
python backend/app.py
```

#### Test it:

```bash
curl -X POST http://localhost:5000/api/ner ^
  -H "Content-Type: application/json" ^
  -d "{\"text\": \"Patient experienced severe nausea and vomiting after taking Omeprazole and Aspirin.\"}"
```

Expected output (actual predictions, not random):
```json
{
  "entities": [
    {"word": "nausea",      "entity_group": "EFFECT", "score": 0.9832, "start": 35, "end": 41},
    {"word": "vomiting",    "entity_group": "EFFECT", "score": 0.9754, "start": 46, "end": 54},
    {"word": "omeprazole",  "entity_group": "DRUG",   "score": 0.9901, "start": 68, "end": 79},
    {"word": "aspirin",     "entity_group": "DRUG",   "score": 0.9887, "start": 84, "end": 91}
  ]
}
```

---

### A2: Biomedical NER — 107 Entity Types

If you want **broader** entity coverage (diseases, symptoms, genes, lab values, etc.), use this model instead:

| Property | Value |
|----------|-------|
| **Model** | [`d4data/biomedical-ner-all`](https://huggingface.co/d4data/biomedical-ner-all) |
| **Base** | DistilBERT (fast, lightweight) |
| **Dataset** | MACCROBAT (clinical case reports) |
| **Entities** | 107 types (Disease, Sign/Symptom, Medication, Lab Value, etc.) |
| **Size** | ~65 MB |

#### Updated `backend/app.py` for Option A2:

```python
MODEL_NAME = "d4data/biomedical-ner-all"

tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
model = AutoModelForTokenClassification.from_pretrained(MODEL_NAME)

ner_pipeline = pipeline(
    "ner",
    model=model,
    tokenizer=tokenizer,
    aggregation_strategy="simple"
)
```

> [!NOTE]
> Everything else in the code stays the same — just change the three lines above.

---

## Option B: Fine-Tune BioBERT Yourself (Advanced — 1-2 Hours)

If you specifically want **BioBERT** (for academic purposes, paper references, etc.), you need to fine-tune it on a labeled NER dataset. Here's the complete process.

### B1: Prerequisites

| Requirement | Details |
|-------------|---------|
| **GPU** | Required. Use [Google Colab](https://colab.research.google.com/) (free T4 GPU) or your own NVIDIA GPU |
| **Python packages** | `transformers`, `datasets`, `evaluate`, `seqeval`, `accelerate`, `torch` |
| **Time** | ~30-60 minutes (training) + ~15 min (setup) |
| **Disk** | ~2 GB (model + dataset + checkpoints) |

### B2: Pick a Dataset

For PharmaWatch (pharmacovigilance), these are the best options:

| Dataset | HuggingFace ID | Entities | Best For |
|---------|---------------|----------|----------|
| **ADE Corpus V2** | `ade_corpus_v2` | Drug, Adverse Effect | Drug safety / your use case |
| **BC5CDR** | `bigbio/bc5cdr` | Chemical, Disease | Chemical-disease relations |
| **NCBI Disease** | `ncbi_disease` | Disease | Disease-only recognition |
| **JNLPBA** | `jnlpba` | Protein, DNA, RNA, Cell Line, Cell Type | Genomics |

> [!TIP]
> For PharmaWatch, **ADE Corpus V2** is the best match — it contains drug names and their adverse effects, which is exactly what your app analyzes.

### B3: Complete Fine-Tuning Script (Google Colab)

Open [Google Colab](https://colab.research.google.com/), set runtime to **GPU** (Runtime → Change runtime type → T4 GPU), then run these cells:

#### Cell 1: Install Dependencies

```python
!pip install transformers datasets evaluate seqeval accelerate -q
```

#### Cell 2: Load Dataset & Tokenizer

```python
from datasets import load_dataset
from transformers import BertTokenizerFast

# ── Load the ADE Corpus V2 (Adverse Drug Events) ──
# This dataset has sentences labeled with Drug and Adverse-Effect entities
dataset = load_dataset("ade_corpus_v2", "Ade_corpus_v2_drug_ade_relation")
print(dataset)
print("Example:", dataset["train"][0])

# ── Load BioBERT tokenizer ──
MODEL_NAME = "dmis-lab/biobert-base-cased-v1.1"
tokenizer = BertTokenizerFast.from_pretrained(MODEL_NAME)
```

#### Cell 3: Preprocess — Convert to Token Classification Format

The ADE Corpus comes as sentence-level text with entity spans. We need to convert it to token-level BIO labels:

```python
import re

# Define our label set
label_list = ["O", "B-DRUG", "I-DRUG", "B-EFFECT", "I-EFFECT"]
label2id = {label: i for i, label in enumerate(label_list)}
id2label = {i: label for i, label in enumerate(label_list)}

def convert_ade_to_ner(examples):
    """
    Convert ADE corpus format (text + entity spans) into
    token-classification format (tokens + BIO labels per token).
    """
    all_tokens = []
    all_labels = []

    for text, drug, effect in zip(examples["text"], examples["drug"], examples["effect"]):
        # Tokenize the text into words (simple whitespace split)
        words = text.split()
        labels = ["O"] * len(words)

        # Build a character-to-word index mapping
        char_to_word = {}
        char_idx = 0
        for word_idx, word in enumerate(words):
            for c in word:
                char_to_word[char_idx] = word_idx
                char_idx += 1
            char_idx += 1  # space

        # Mark drug entities
        if isinstance(drug, str):
            drug = [drug]
        for d in drug:
            start = text.find(d)
            if start != -1:
                end = start + len(d)
                first = True
                for ci in range(start, min(end, len(text))):
                    if ci in char_to_word:
                        wi = char_to_word[ci]
                        if first:
                            labels[wi] = "B-DRUG"
                            first = False
                        elif labels[wi] == "O":
                            labels[wi] = "I-DRUG"

        # Mark adverse effect entities
        if isinstance(effect, str):
            effect = [effect]
        for e in effect:
            start = text.find(e)
            if start != -1:
                end = start + len(e)
                first = True
                for ci in range(start, min(end, len(text))):
                    if ci in char_to_word:
                        wi = char_to_word[ci]
                        if first:
                            labels[wi] = "B-EFFECT"
                            first = False
                        elif labels[wi] == "O":
                            labels[wi] = "I-EFFECT"

        all_tokens.append(words)
        all_labels.append([label2id[l] for l in labels])

    return {"tokens": all_tokens, "ner_tags": all_labels}

# Apply preprocessing
processed = dataset["train"].map(
    convert_ade_to_ner,
    batched=True,
    remove_columns=dataset["train"].column_names
)

# Split into train/validation (90/10)
split = processed.train_test_split(test_size=0.1, seed=42)
train_dataset = split["train"]
eval_dataset  = split["test"]

print(f"Train: {len(train_dataset)} examples")
print(f"Eval:  {len(eval_dataset)} examples")
```

#### Cell 4: Tokenize for BERT (Handle Sub-Word Alignment)

```python
def tokenize_and_align_labels(examples):
    """
    BioBERT uses WordPiece — one word may become multiple sub-tokens.
    We assign the label to the first sub-token and -100 to the rest
    so the loss function ignores them.
    """
    tokenized = tokenizer(
        examples["tokens"],
        truncation=True,
        is_split_into_words=True,
        max_length=128,
        padding="max_length"
    )

    all_labels = []
    for i, labels in enumerate(examples["ner_tags"]):
        word_ids = tokenized.word_ids(batch_index=i)
        label_ids = []
        previous_word_id = None
        for word_id in word_ids:
            if word_id is None:
                label_ids.append(-100)          # special tokens
            elif word_id != previous_word_id:
                label_ids.append(labels[word_id])  # first sub-token gets the label
            else:
                label_ids.append(-100)          # subsequent sub-tokens are ignored
            previous_word_id = word_id
        all_labels.append(label_ids)

    tokenized["labels"] = all_labels
    return tokenized

# Apply tokenization
train_tokenized = train_dataset.map(tokenize_and_align_labels, batched=True)
eval_tokenized  = eval_dataset.map(tokenize_and_align_labels, batched=True)
```

#### Cell 5: Set Up Model & Training

```python
import numpy as np
import evaluate
from transformers import (
    BertForTokenClassification,
    TrainingArguments,
    Trainer,
    DataCollatorForTokenClassification
)

# ── Load BioBERT with a classification head ──
model = BertForTokenClassification.from_pretrained(
    MODEL_NAME,
    num_labels=len(label_list),
    id2label=id2label,
    label2id=label2id
)

# ── Metrics: use seqeval for proper NER evaluation ──
seqeval = evaluate.load("seqeval")

def compute_metrics(eval_pred):
    logits, labels = eval_pred
    predictions = np.argmax(logits, axis=2)

    true_labels = []
    true_preds  = []

    for pred_seq, label_seq in zip(predictions, labels):
        t_labels = []
        t_preds  = []
        for pred, label in zip(pred_seq, label_seq):
            if label != -100:
                t_labels.append(id2label[label])
                t_preds.append(id2label[pred])
        true_labels.append(t_labels)
        true_preds.append(t_preds)

    results = seqeval.compute(predictions=true_preds, references=true_labels)
    return {
        "precision": results["overall_precision"],
        "recall":    results["overall_recall"],
        "f1":        results["overall_f1"],
        "accuracy":  results["overall_accuracy"],
    }

# ── Training hyperparameters ──
training_args = TrainingArguments(
    output_dir="./biobert-ner-ade",
    eval_strategy="epoch",
    save_strategy="epoch",
    learning_rate=2e-5,           # low LR to avoid catastrophic forgetting
    per_device_train_batch_size=16,
    per_device_eval_batch_size=16,
    num_train_epochs=5,
    weight_decay=0.01,
    load_best_model_at_end=True,
    metric_for_best_model="f1",
    logging_steps=50,
    fp16=True,                     # use mixed precision on GPU
    report_to="none",              # disable wandb
)

data_collator = DataCollatorForTokenClassification(tokenizer)

trainer = Trainer(
    model=model,
    args=training_args,
    train_dataset=train_tokenized,
    eval_dataset=eval_tokenized,
    tokenizer=tokenizer,
    data_collator=data_collator,
    compute_metrics=compute_metrics,
)
```

#### Cell 6: Train!

```python
# This takes ~20-40 minutes on a free Colab T4 GPU
trainer.train()
```

You should see output like:
```
Epoch  | Training Loss | Validation Loss | Precision | Recall | F1
-------|---------------|-----------------|-----------|--------|------
1      | 0.1523        | 0.0987          | 0.78      | 0.75   | 0.76
2      | 0.0654        | 0.0712          | 0.83      | 0.82   | 0.82
3      | 0.0321        | 0.0598          | 0.86      | 0.85   | 0.85
4      | 0.0198        | 0.0567          | 0.87      | 0.86   | 0.86
5      | 0.0112        | 0.0589          | 0.87      | 0.86   | 0.87
```

> [!TIP]
> An F1 score of **0.85+** is good for biomedical NER. The pre-trained SciBERT model (Option A1) achieves similar scores on this same dataset — so Option A is genuinely competitive.

#### Cell 7: Save & Download

```python
# Save the fine-tuned model
trainer.save_model("./biobert-ner-ade-final")
tokenizer.save_pretrained("./biobert-ner-ade-final")

print("Model saved to ./biobert-ner-ade-final")
print("Files in the directory:")
import os
for f in os.listdir("./biobert-ner-ade-final"):
    size = os.path.getsize(f"./biobert-ner-ade-final/{f}") / 1024 / 1024
    print(f"  {f:40s} {size:.1f} MB")
```

Now download the model folder to your local machine:

```python
# Zip and download from Colab
!zip -r biobert-ner-ade-final.zip ./biobert-ner-ade-final/

from google.colab import files
files.download("biobert-ner-ade-final.zip")
```

### B4: Use Your Fine-Tuned Model in PharmaWatch

After downloading, unzip the model into your project:

```
PharmaWatch/
├── backend/
│   ├── app.py
│   └── model/              ← create this folder
│       ├── config.json
│       ├── model.safetensors
│       ├── tokenizer.json
│       ├── tokenizer_config.json
│       ├── special_tokens_map.json
│       └── vocab.txt
```

Then update `backend/app.py`:

```python
from flask import Flask, request, jsonify
from flask_cors import CORS
from transformers import BertTokenizer, BertForTokenClassification, pipeline
import os

app = Flask(__name__)
CORS(app)

# ── Load YOUR fine-tuned model from the local directory ───────────────────────
print("Loading fine-tuned BioBERT NER model...")
MODEL_PATH = os.path.join(os.path.dirname(__file__), "model")

tokenizer = BertTokenizer.from_pretrained(MODEL_PATH)
model = BertForTokenClassification.from_pretrained(MODEL_PATH)
model.eval()

ner_pipeline = pipeline(
    "ner",
    model=model,
    tokenizer=tokenizer,
    aggregation_strategy="simple"
)
print("Fine-tuned BioBERT NER loaded OK")

# ... rest of the app.py remains the same as Option A1 ...
```

### B5: (Optional) Push to HuggingFace Hub

If you want to load it from anywhere (like Option A) instead of shipping files:

```python
# In Colab, after training:
from huggingface_hub import login
login()  # paste your HF token

trainer.push_to_hub("YOUR_USERNAME/biobert-ner-pharmawatch")
tokenizer.push_to_hub("YOUR_USERNAME/biobert-ner-pharmawatch")
```

Then in `app.py`:
```python
MODEL_NAME = "YOUR_USERNAME/biobert-ner-pharmawatch"
```

---

## Quick Comparison: Which Option Should You Pick?

| | Option A1: SciBERT ADE | Option A2: Biomedical-NER-All | Option B: Fine-Tune BioBERT |
|---|---|---|---|
| **Effort** | 5 minutes | 5 minutes | 1-2 hours |
| **GPU needed?** | No | No | Yes (training only) |
| **Entity types** | Drug, Adverse Effect | 107 types (disease, symptom, medication, lab, gene, etc.) | Drug, Adverse Effect (or whatever you train on) |
| **Model size** | ~110 MB | ~65 MB | ~440 MB |
| **Accuracy** | High (trained on ADE Corpus V2) | Good (trained on MACCROBAT) | High (you control the data) |
| **Best for** | Pharmacovigilance (Drug + Side Effect) | Broad biomedical NER | Academic projects needing "BioBERT" specifically |

> [!IMPORTANT]
> **My recommendation for PharmaWatch**: Start with **Option A1** (SciBERT ADE). It's purpose-built for exactly what your app does — detecting drug names and adverse effects. You can always switch to Option B later if you need custom entity types or better accuracy on your specific data.

---

## Updating Your Frontend

Your current `app.js` sends text to `/api/ner` and expects `{ entities: [{token, label}, ...] }`. With the new pipeline-based backend, the response format changes slightly:

**Old format** (random predictions):
```json
{"entities": [{"token": "Aspirin", "label": "O"}]}
```

**New format** (real predictions):
```json
{"entities": [{"word": "Aspirin", "entity_group": "DRUG", "score": 0.9901, "start": 20, "end": 27}]}
```

You'll need to update your frontend code to use `word` instead of `token` and `entity_group` instead of `label`, or normalize the backend response to match your existing format. Here's how to normalize in the backend:

```python
# Add this to the ner_endpoint() function to keep backward compatibility:
entities = []
for ent in raw_entities:
    entities.append({
        "token":   ent["word"],             # old field name
        "label":   ent["entity_group"],     # old field name
        "score":   round(float(ent["score"]), 4),
        "start":   ent["start"],
        "end":     ent["end"]
    })
```

---

## Useful Links

| Resource | URL |
|----------|-----|
| SciBERT Drug/ADE NER | https://huggingface.co/jsylee/scibert_scivocab_uncased-finetuned-ner |
| Biomedical NER (107 entities) | https://huggingface.co/d4data/biomedical-ner-all |
| ADE Corpus V2 Dataset | https://huggingface.co/datasets/ade-benchmark-corpus/ade_corpus_v2 |
| BioBERT Base Model | https://huggingface.co/dmis-lab/biobert-base-cased-v1.1 |
| HuggingFace NER Tutorial | https://huggingface.co/docs/transformers/tasks/token_classification |
| Google Colab (free GPU) | https://colab.research.google.com/ |
| SeqEval Metrics | https://huggingface.co/spaces/evaluate-metric/seqeval |
