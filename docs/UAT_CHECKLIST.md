# PharmaWatch — User Acceptance Test (UAT) checklist

A manual pass through every area, for use before a pilot and by college staff. Research use only;
a disproportionality signal is a reporting pattern, not proof of causation.

**Setup:** start MySQL and the app (`python backend/app.py`), open `http://127.0.0.1:5000`. FAERS data
loaded. Kafka/HBase optional — the app must behave correctly whether they are up or down.

For each step: do the action, confirm the expected result, note anything off.

## 0. Shell
- [ ] Seven areas show in the top navigation: Monitor, Investigate, Interactions, Prescription
      Review, Text Mining, Pipeline, Methods & Validation.
- [ ] A multi-section area (Monitor, Investigate, Methods) shows a sub-tab bar; a single-section
      area hides it.
- [ ] The footer reads "For research and education only…" (no "simulation / illustrative" claim).
- [ ] Open an old link directly, e.g. `/#drug-search` and `/#data-sources`: it lands on that section.

## 1. Monitor
- [ ] **Dashboard** hero counters show real numbers from the loaded data, or "—" if the stats API is
      down — never a fixed fallback like 14,283,740.
- [ ] **Signal Detection** lists signals; clicking a row opens the detail with a **Measured** badge
      (hover shows the data version), the 2×2 table, and "How this signal developed".
- [ ] The detail shows "Open full drug profile →"; clicking it switches to Investigate for that drug.
- [ ] **Surveillance** shows the alert feed and the Replay history tab (Start plays the quarters).

## 2. Investigate
- [ ] **Drug Profile**: search a drug (e.g. Metformin). Header, top reactions chart, and a
      "Combined evidence" card appear with a **Composite** badge, report count, boxed-warning status,
      linked papers/recalls/trials, and surveillance alerts. No fabricated numbers when a store is down.
- [ ] Selecting a drug in Monitor and opening Investigate shows the same drug.
- [ ] **Boxed Warnings**, **Temporal**, **Demographics** each load for a drug.

## 3. Interactions
- [ ] Enter two drugs; the pair result shows a **Modelled** badge and uses "predicted interaction"
      wording (never "is_harmful").

## 4. Prescription Review
- [ ] Upload a sample prescription; extracted lines appear; the honesty caveats are shown.

## 5. Text Mining
- [ ] Paste "No bleeding was observed on warfarin." → **no** warfarin–bleeding mention is produced.
- [ ] Paste a note with a drug and an event in the same sentence → a **Mention** card appears,
      labelled "Mention · text mining", scored against FAERS, with the note that a mention is not a
      signal and never part of a 2×2 count.
- [ ] The model is named a DistilBERT-based biomedical NER model — the word "BioBERT" does not label
      it anywhere.

## 6. Pipeline
- [ ] With Kafka **stopped**: the throughput chart shows "Kafka not running" / empty, source meters
      read "not running", and the Spark/HDFS/HBase panels read "not connected" — no moving numbers.
- [ ] With Kafka **started** and the producer running: topic counts and messages/min update from
      real data.
- [ ] The banner states what is real and that no panel shows a simulated number; "Spark batch" (not
      "Spark Streaming") is the label.

## 7. Methods & Validation
- [ ] The Validation page lists each component (signals, DDI, NER, prescription, scaling, big-data
      timing, label-check, lead-time) with its reference, data version and caveats.
- [ ] The lead-time card shows the four groups separately with CIs and the "pending faculty review"
      caveat.
- [ ] Research Guide and About load.

## 8. Social listening (if configured)
- [ ] Without REDDIT_CLIENT_ID/SECRET set, the Reddit endpoints report "not configured"; the UI says
      so. Any social mentions are labelled exploratory and unverified and never enter a statistic.
