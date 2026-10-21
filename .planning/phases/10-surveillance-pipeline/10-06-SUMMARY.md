# 10-06 Summary: Navigation regroup, drug profile, evidence badges

Makes the ten-plus tabs behave as one tool: seven top-level areas, one drug profile that combines
every kind of evidence, and a provenance badge primitive used on the main result cards. Committed
in three parts (39baf6e, a4d9d8e, 282bc84).

## Task 1 — Seven-area navigation (done)
- Top nav now shows seven areas; the active area's sub-tabs render into a secondary bar from
  `NAV_AREAS` in `app.js`. **Monitor** (dashboard, signals, surveillance), **Investigate**
  (drug profile, boxed warnings, temporal, demographics), **Interactions**, **Prescription Review**,
  **Text Mining**, **Pipeline** (data sources), **Methods & Validation** (validation, methodology,
  research guide, about).
- Every section that existed is still reachable; no section's internals changed — only where it
  sits. The Clinical NER mining card moved out of Signal Detection into its own **Text Mining**
  section (ids preserved; `initNerPanel` still binds at load).
- Old `#hash` links and bookmarks work: honored on first load (new) and on `hashchange`.
- Footer's "research simulation / data is illustrative" line replaced with a research-use-only
  statement (no model/causation claims).
- Verified in headless Chrome: 7 areas render, initial-hash navigation works, no console errors.

## Task 2 — Drug profile (done)
- `GET /api/drug/<name>/profile` (`services/drug_profile.py`, `routes/drug.py`) resolves the name
  **exactly once** (`signals.resolve_drug`), then assembles identity, report count, top reactions,
  boxed-warning status, trials, external evidence and surveillance alerts by the resolved rxcui. It
  computes nothing itself. Added `faers.analytics.get_report_count` and a resolve-free
  `evidence.links_for_rxcuis` so the single-resolution guarantee holds; surveillance alerts are read
  by rxcui directly. A store being down degrades one section, not the profile.
- Frontend: `loadDrugProfile` renders a combined-evidence card from the endpoint (report count,
  boxed-warning status, linked papers/recalls/trials, surveillance alerts). `DrugContext` carries
  the selected drug across areas: selecting a signal in Monitor sets it, exposes "Open full drug
  profile", and entering Investigate auto-loads the selected drug.
- 3 tests (`test_drug_profile.py`): one resolution per request, graceful degrade when a drug does
  not resolve, and "no FAERS data" stated not faked. Verified on real data: Metformin → 40,086
  reports, lactic acidosis the top reaction, boxed warning detected, meta = composite with the data
  version.

## Task 3 — Evidence badges (done, primary cards; see limit)
- `ApiLayer.renderEvidence(meta)` returns a small badge — **Measured / Modelled / Retrieved /
  Operational / Composite** (the actual backend evidence kinds; there is no invented "Simulated"
  class) — with a tooltip giving the data version and model version. No `meta` → "provenance
  unavailable".
- Attached to the primary result cards: drug profile (composite), signal detail (measured, with the
  signals data version) and the interaction pair result (modelled). The primitive is available
  app-wide for the remaining cards.
- Removed **every** `is_harmful` read from `src/` (grep: none remain); interaction code uses
  `predicted_interaction` only, with the normalized graph field renamed `harmful`.

## Verified
- Full backend suite green (run after the three commits, MySQL up); route inventory + provenance +
  frontend-wording + architecture all pass; 3 new drug-profile tests pass.
- Headless Chrome: seven areas, initial-hash nav, drug profile endpoint on real data, no console
  errors.

## Limits and open points
- **Badge coverage is on the primary result cards, not literally every card.** The reusable
  `renderEvidence` primitive is in place; attaching it to the remaining charts/cards (temporal,
  demographics, dashboard tiles, prescription findings, text-mining results) is a mechanical
  follow-up and is **not** complete — the must-have "every result shows a badge" is only partly met.
- The profile's temporal/demographic/boxed-warning panels are surfaced as a combined-evidence card
  and via cross-area selection, not embedded as full in-page analyses; the dedicated areas remain
  the place for the full charts.
- Evidence and alert sections of the profile need MySQL; they degrade to an empty/"unavailable"
  state when it is down (observed and handled).
