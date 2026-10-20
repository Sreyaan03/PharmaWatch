# 10-05 Summary (Part A): Replay history through the pipeline

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
