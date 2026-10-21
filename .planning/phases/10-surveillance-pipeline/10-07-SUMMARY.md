# 10-07 Summary: Text mining corrected, Pipeline made honest, contract test

Fixes the last places where labels overstated the code, with the existing look, fonts and icons
left unchanged — only logic, wording and data honesty changed.

## Task 1 — Text mining (done)
- New `services/text_mining.py` (pure, tested): `pair_mentions(text, entities)` forms a drug-event
  pair only when both occur in the **same sentence**, and drops an event under a negation cue
  ("no", "not", "denies", "without", "ruled out", "negative for", …) that appears earlier in that
  sentence. `resolve_and_score` resolves each mention through the identity service; only resolved
  pairs are scored against FAERS, unresolved ones are listed as such.
- Mined pairs are stored with source **`text_mining`** and shown as **mentions**, separate from
  signals; they are never upserted as signals and never enter a 2×2 count (the 2×2 always comes from
  FAERS). The old whole-text cartesian pairing + `upsert_signal(source="ner_clinical")` +
  `source="biobert"` writes are gone (`signals.ner_endpoint` and `_mine_text` rewritten).
- Model renamed in the UI and guide to what it is — a **DistilBERT-based biomedical NER model**
  (`d4data/biomedical-ner-all`). The string "BioBERT" no longer labels the model anywhere in `src/`
  (JS identifiers, element ids and the `biobert.js` filename are kept). The frontend NER results
  renderer shows mentions (PRR/ROR-lower/IC025 from FAERS) with the "mention, not a signal" note.
- 6 unit tests (`test_text_mining.py`): negation ("No bleeding … on warfarin" → no pair), same- vs
  cross-sentence pairing, "denies" cue, multiple events, negation scoped to its own sentence.

## Task 2 — Social listening & Pipeline honesty (done)
- `reddit_scraper.py`: credentials now come from the environment (`REDDIT_CLIENT_ID/SECRET/
  USER_AGENT` in `core/config`); the hard-coded `YOUR_CLIENT_ID` placeholders are gone.
  `reddit_configured()` gates everything; `trigger_scrape`/`trigger_processing` return
  `"not_configured"` with a note when unset. Posts go through the same text-mining path (mentions,
  source `text_mining`), labelled exploratory and unverified, never into statistics.
- `distributed_storage.js`: all `Math.random` simulations removed (Spark micro-batch, HDFS/HBase
  ops, the random throughput series and its ticker). The throughput chart now plots **real**
  messages/min per topic from `/api/pipeline/stats`, or "Kafka not running"; source meters show real
  ingested totals or "not running"; Spark reads "Spark batch — runs offline", HDFS/HBase read
  "not connected (no cluster)". "Spark Streaming" renamed to **Spark batch** throughout.
- `app.js`: the fabricated dashboard counter fallback (14,283,740 / 47 / 12,490) is gone — counters
  show real values or "—"; the dead fake `animateMeters` (hard-coded msg/min) was removed. The
  Pipeline banner now states what is real and that no panel shows a simulated number.

## Task 3 — Frontend contract test + UAT (done, with one deferral)
- `test_frontend_contract.py`: every same-origin `/api/...` path referenced in `src/*.js` and
  `index.html` matches a rule in `app.url_map` (template `${…}` handled; external URLs excluded);
  every navigation target (data-section/-nav and the NAV_AREAS section ids) has a section id; every
  `<script src>` points to a file that exists. Fails if a frontend path is misspelt.
- `docs/UAT_CHECKLIST.md`: a step-by-step manual pass through all seven areas with expected results.
- **Deferred:** switching the frontend to the newer of each Phase-5 aliased route pair and removing
  the older aliases — not done here (needs the alias inventory and is riskier than the honesty
  fixes); the contract test already guards against misspelt/removed paths, so this can follow safely.

## Verified
- `test_text_mining` (6) and `test_frontend_contract` (3) pass; config/.env, no-invented-numbers,
  architecture and frontend-wording pass; route inventory consistent. Full suite green.
- provenance model tag renamed `biobert-ner` → `biomedical-ner` for the NER routes.

## Limits / open points
- `resolve_and_score` is covered by the pure pairing tests; its FAERS scoring path is exercised only
  through the running app (needs DuckDB), not a unit test.
- The Pipeline area does not yet show the last Spark run's time/rows or HBase table size inline (the
  plan's fuller "live state" list); those remain on the Methods page. Panels correctly read
  "not connected" instead of inventing them.
- The deprecated Phase-5 route aliases still exist (deferred, above).
