# 10-05 Summary

Part A (replay + timeline) and Part B (lead-time study) are both done. Part B's section is at the
bottom; Part A follows immediately below.

---

# Part A: Replay history through the pipeline

The loaded quarters can be replayed through the surveillance pipeline, in order, in a few minutes.
A replay feeds each quarter's precomputed Spark results back through the **same** alert detector
that runs live, writing to an isolated store keyed by a run id. It changes no live data. These are
the alerts the system would have raised at the time.

**Part A (Tasks 1–2) is done.** Part B (Tasks 3–4, the lead-time study) is a checkpoint: it needs a
dated, PT-mapped list of FDA safety labelling changes, which requires a manual download / possibly
an account. Deferred until the user decides on the data source — see "Pending" below.

## Done
- **Replay store** (`db/schema.sql`: `replay_runs`, `replay_alerts`). One alert row per
  (run, quarter, kind, rxcui, pt), so re-stepping a quarter changes nothing. Never written outside
  replay; the live `surveillance_alerts` table is untouched (verified).
- **Replay service** (`services/surveillance.py`). The live detector was refactored into shared
  helpers (`_classify_and_rank`, `_alert_detail`, `_kind_for`, `_quarters`) so replay and the live
  path run identical logic. New functions: `start_replay(from, to)` (creates a run over a quarter
  span), `replay_step(run_id, quarter)` (computes one quarter into the replay store; idempotent),
  `replay_status(run_id)` (span, status, per-quarter counts for the timeline), and
  `list_replay_alerts(run_id, quarter=, upto=)` (the feed, cumulative or per quarter). Cap is 100
  alerts/quarter for a quick demo; the true newly-signalling total is reported.
- **Kafka path** (`ingest/consumer.py`: `replay-signals` topic → `handle_replay_signals`). When the
  bigdata profile is up, one event per quarter drives the detector through Kafka, into the replay
  store. The genuine pipeline path, isolated to replay.
- **CLI** (`scripts/replay.py`). `python scripts/replay.py --from 2021q3 --to 2026q2
  --seconds-per-quarter 10` publishes one `replay-signals` event per quarter (Kafka); `--inline`
  runs each quarter in-process for a laptop with no Kafka up.
- **Routes** (`routes/surveillance.py`): `POST /api/surveillance/replay/start`,
  `POST /api/surveillance/replay/<run_id>/step`, `GET /api/surveillance/replay/<run_id>`
  (`?quarter=` or `?upto=` adds the alerts). Not admin-gated (a demo feature that writes no live
  data); they degrade cleanly (400/404/503) when the lake or MySQL is absent.
- **Replay view** (`src/index.html`, `src/surveillance.js`). A "Replay history" tab in Surveillance,
  separate from the live feed: Start / Pause / Resume, a speed control, a quarter timeline that
  marks the current quarter and shows new-alert counts per quarter as they arrive, a running
  progress line, and the feed filling cumulatively. The browser drives the timeline tick by tick
  via the step endpoint, so it works on the laptop with no Kafka. A blue banner states it is a
  replay of historical data that changes no live data.

## Verified
- `tests/test_replay.py` (5 tests) + `tests/test_surveillance.py`: **11 passed**. Covers: a replay
  populates the replay store (incl. the strong 111/rhabdomyolysis signal), a replay never writes to
  `surveillance_alerts`, two replays of the same range match, and re-stepping a quarter is
  idempotent.
- Architecture + frontend-wording tests pass; route inventory regenerated (3 new routes, all with
  graceful error shapes) and passing.
- End to end on the real 20-quarter lake (2021q3–2026q2) through the running app's HTTP routes:
  replayed 2023q1–q4, 100 alerts/quarter (true totals ~30–33k shown), not-in-label ranked first,
  cumulative feed correct, status → done. **Live alert count 300 before and 300 after** — isolation
  confirmed through the real web path.
- Headless Chrome load of the Surveillance page: replay tab renders, no JS console errors.

## Pending (Part B — checkpoint)
- **Task 3 (reference source).** Build `backend/validation/reference/label_changes.csv`: drug,
  effect added (→ MedDRA PT), section, date, from FDA's Drug Safety-related Labeling Changes (SrLC)
  or DailyMed version history. Needs a manual download / possibly an account — **ask the user first**.
- **Task 4 (lead-time study).** `backend/validation/lead_time.py`: for each label change, the first
  quarter the pair was a signal vs the label-change date; report flagged-before (with lead time),
  flagged-after, left-censored (signal already present in 2021q3) and never-flagged **separately**,
  with bootstrap CIs; wire into `run_all` and the Methods page with the four caveats (20 quarters
  only; stimulated reporting; FAERS receipt dates; label changes are not a complete truth set).

## Notes / limits
- Each replay step runs a DuckDB query over the lake, so a step takes a few seconds on the real
  data; the "speed" control is a floor between ticks, not a hard cadence.
- Replay alerts carry no review buttons (they are historical, not a live queue) and inherit the
  10-03 first-signal-quarter dating caveat.
- `replay-signals` idempotency is covered by `test_replay`, not the 10-01 replay test (it needs the
  lake), the same arrangement as `signals-updated`.

---

# Part B: Lead-time study — did FAERS signal before the FDA label changed?

Turns the replay into a measured answer. For each drug-event pair the FDA added to a safety section
of a label (2021Q3-2026Q2), we find the first quarter the pair was a signal in our Spark results and
compare it with the quarter of the label change. Four groups are reported **separately, never
merged**, with bootstrap CIs. The reference is **author-built and pending faculty review**.

## Done
- **Label-change reference** (`validation/fetch_srlc.py`, `reference/srlc_index.csv` ->
  `reference/label_changes.csv`). From FDA's Drug Safety-related Labeling Changes (SrLC) export,
  kept the drugs that map to a FAERS ingredient we hold (salt-stripped, combos split) and changed in
  window, fetched all **1,636** detail pages (0 errors, cached under `DATA_DIR/srlc_cache`), and
  parsed every safety-section change: date, section (boxed warning / warnings / contraindications /
  adverse reactions), and the **underlined added text**. The added text is matched to FAERS Preferred
  Terms with the same `MentionIndex` the consumer uses, giving **3,771 change rows -> 3,052 distinct
  (ingredient, PT) label-change pairs** in our own vocabulary. Mapping is automated and marked
  PENDING FACULTY REVIEW.
- **Study** (`validation/lead_time.py`, pure `compute()` + IO). First-signal quarter per (rxcui,
  pt_lower) from the Spark results (cumulative, `signal_strength <> 'none'`). Each pair is classified
  flagged-before (with lead time in quarters) / flagged-at-or-after / left-censored (already a signal
  in the earliest loaded quarter) / never-flagged. Share flagged-before and median lead time each
  carry a 2,000-sample bootstrap 95% CI. Writes `reports/validation/lead_time.json` (+ a
  `lead_time_pairs.json` sidecar with per-pair detail, kept out of the published summary).
- **Wired in**: `report.py` renders a "Early-warning lead time vs FDA label changes" component (four
  groups, share+CI, median+CI, caveats) and a new known-limitation; `run_all.py` runs it as a step
  that skips cleanly when the lake or `label_changes.csv` is absent; the Methods page shows it with
  no frontend change (components render generically). `provenance.py` classifies the replay routes.
- 6 unit tests (`test_lead_time.py`) cover each group, the lead-time arithmetic, the left-censoring
  rule, a label change dated before the window, and the share/median/distribution.

## Measured result (real data, 3,052 pairs)
- **Flagged before the label change: 592 (19.4%, 95% CI 18.1-20.8%)**, median lead **7 quarters**
  (CI 7-8).
- Flagged at or after: 322. Left-censored: 607. Never flagged: 1,531 (~50%).

Read honestly: about a fifth of label-changed pairs had a FAERS signal first, but half were never a
signal in our 20 quarters and a fifth were left-censored. The 7-quarter median lead largely reflects
long-known associations that signalled early and were re-labelled years later (stimulated reporting /
known associations), **not** proof of early detection of novel harms. This is agreement-timing with
FDA labelling, not a sensitivity claim.

## Limits and open points
- Reference mapping is automated (PT mention match) and **pending faculty review**; a wrong
  label-text -> PT mapping mis-scores a pair. SrLC covers brand NDA/BLA products, not generics.
- 20 quarters only; FAERS receipt dates; label changes are not a complete truth set; reporting rises
  after a change. All four caveats are shown on the Methods page.
- The underlined-additions parse can miss changes a label marked by other means; a few SrLC entries
  omit the added text ("refer to label") and yield no PT.
