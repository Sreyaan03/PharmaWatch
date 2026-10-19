# 10-04 Summary: Surveillance — label watcher, label-text check, alerts

The stream is now surveillance: Kafka watches FDA labels, each signal is checked against the
label, and new signals and label changes become reviewable alerts. Everything is worded as a
possible signal for review, never a finding.

## Done
- **Label watcher** (`ingest/label_watcher.py`, `clients/openfda.fetch_label_sections`). Once a
  day the producer fetches each watched drug's FDA label (boxed warning, warnings, precautions,
  contraindications, adverse reactions), stores a snapshot (`label_snapshots`), and when the
  version or effective time changes publishes the **added sentences** on `label-change`. The first
  fetch of a drug stores a baseline only, so the feed does not flood. Watched = the watchlist plus
  the strongest-signal drugs in the latest quarter (from Spark results), capped at 150/run.
  A sentence-diff ignores whitespace and reordering. 10 tests.
- **Label-text check** (`services/label_check.py`). `find(rxcui, pt)` searches the stored label
  sections for the Preferred Term, British/American spellings, two-word order ("neuropathy
  peripheral" ↔ "peripheral neuropathy"), singular/plural, and reviewed synonyms, returning
  `in_label_text` (with section and sentence), `not_found_in_label_text`, or `no_label`. The wording
  is never "unlabelled". 11 tests.
- **Measured accuracy.** `validation/label_check_benchmark.py` against an author-labelled gold set
  of 40 drug-event pairs across 8 drugs (`reference/label_check_gold.csv`, marked *pending faculty
  review*), labelled by reading the actual FDA labels independently of the matcher. For the
  reviewer-facing class "not found in label text": **precision 0.41, recall 0.875, accuracy 0.73**.
  The low precision is real and informative: the simple matcher flags effects as "not in label"
  when the label describes them in other words (hepatotoxicity → "hepatic dysfunction", anaphylactic
  reaction → "anaphylaxis", adrenal insufficiency → "HPA-axis suppression"). Published on the
  Methods page with that caveat.
- **Alert detector** (`services/surveillance.py`). On `signals-updated` it reads the Spark results
  (computes no statistics), finds pairs that became a signal in the latest quarter (not a signal in
  the previous one), and raises alerts: `not_in_label` when the stored label does not mention the
  effect, else `new_signal`. On `label-change` it raises a `label_change` alert and links it to open
  signal alerts for the same drug whose term now appears in the added text ("the label caught up").
  A quarter brought 29,678 newly-signalling pairs on real data, so alerts are capped (300/run),
  not-in-label first then by IC025, with the true total recorded. Product-use and dosing-error
  terms are filtered out. Every write is an upsert on (kind, rxcui, pt, quarter): replaying an
  event changes nothing (tested). 8 tests.
- **Surveillance area** (`routes/surveillance.py`, `src/surveillance.js`, new nav section). Alert
  feed with a badge per kind, the evidence (IC025, cases, PRR, the quarter it became a signal), the
  label sentence or "not found in label text" with the label's date, review buttons (reviewed /
  escalate / dismiss), a shared watchlist, and a status line (alert counts, last batch quarter,
  data version). Each signal alert links to its history chart. Filters by kind, status, drug.
  Checked in headless Chrome on real data: 300 alerts, not-in-label ranked first, no JS errors.
- **Honest wording guard extended**: the pages may never say "confirmed signal", "new side effect
  discovered", "unlabelled", or "proven causal" (test_frontend_wording).
- **Consumer** handles `signals-updated` and `label-change`; it is an event-driven orchestrator
  (like the workers), so the architecture test now lets `ingest` call `services` but never Flask.

## Verified on real data
- Seeded labels for 37 drugs, ran the detector: 144 not-in-label and 156 new-signal alerts, e.g.
  "tryptophan — Bronchial hyperreactivity (not found in the L-TRYPTOPHAN label)".
- Watchlist add/remove, review-status change, and provenance meta all work through the app.
- 502 tests pass; CI lint clean.

## Limits and open points
- The gold set is 40 pairs, author-labelled, **pending faculty review**; it is a small check, not a
  licensed-MedDRA audit. The matcher's recall for "not in label" is good but its precision is low
  because labels paraphrase — a licensed MedDRA SMQ/LLT mapping would improve it.
- Alerts are capped at 300/run; the true newly-signalling total is shown but most pairs get no
  alert row. Not-in-label alerts only appear for drugs whose label has been fetched (watchlist +
  top-signal drugs).
- "Newly a signal in quarter Q" inherits the 10-03 caveat: a case counts from the quarter of its
  latest report version, so the first-signal quarter is conservative (late).
- The Docker consumer needs the lake (Spark results) available to run the detector; the compose
  files mount only the FAERS database today (the data story uses C:\PharmaWatchData via .env). A
  deployment follow-up, same gap as the rest of the data mounts.
- `label-change` and `signals-updated` are not in the idempotency replay test of 10-01 (they need
  the lake); their idempotency is covered by test_surveillance instead.
