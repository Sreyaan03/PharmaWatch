# Summary 07-03: Descriptive Boxed-Warning Features, Normalised Forecast

## What was built
- **Warning years are sourced.** `HISTORICAL_WARNING_YEARS` (about 200 unsourced entries) and `_get_true_warning_year` are deleted. A year comes only from `backend/data/boxed_warning_dates.csv` (`ingredient, year, source_url, verified_by`; a row with no source is ignored) or from a `?warning_year=` parameter, and the response says which (`warning_year_source`). The file ships with the header only; colleges add entries with a source. The label's `effective_time` is returned separately as `label_revision_date`. A drug with no boxed warning gets no year, even if one is supplied, and no warning-relative analysis is run.
- **Event matching** (`services/warning_rules.py`): a reported term matches the warning text only as a whole phrase, or through a reviewed synonym from `faers/event_synonyms.csv`. "Pain" in a warning no longer matches "Abdominal pain upper"; a four-letter word shared with the term is not a match. Each match records `matched_by` (`term` or `synonym`).
- **Descriptive wording**: "violations" is now "post-warning reports of labelled events", at `/api/boxed-warning/post-warning-reports/<drug>` and `/all`; the old paths stay as aliases returning the same data (old key names are kept beside the new ones). Without a sourced year, reports are not split before/after and nothing is persisted. The timeline states slopes and a reporting caveat instead of a conclusion about prescribing; the Weber analysis is labelled descriptive (sourced-year window, or relative pattern); the trial cross-reference says a registered trial is context, not confirmation (`trial_context` replaces `corroboration`).
- **Year ceiling**: per-year counts were cut off at 2025; they now run to the current year.
- **Forecast on reporting share** (`ml/forecast.py`, `services/forecasting.py`, `faers.stats.monthly_report_share`): a drug's monthly percentage of all reports received, from the loaded FAERS data. Chronological split: train (LSTM fitted here only), validation (calibrates the interval), last 12 months (test). Baseline is the same month a year earlier; the LSTM is shown only if it beats that baseline on the test window. A model trained on other data is reported `stale`. Interval is max(validation RMSE, binomial noise of the month's report total) x 1.96; a month is flagged only above the interval, and a run is reported after 3 in a row. `rising_signal`, `trend_slope` and `risk_level` are removed; the gap scan makes no statement about needing a boxed warning. The padding of a short series (`counts x 5`) and the flat placeholder chart are removed.
- **Frontend and guide** text updated to match (cards, badges, table headers, Methodology paragraph).

## Verification
- `test_boxed_warnings.py` (23): sourced dates only, implausible years ignored, matching rules, no-warning drug never gets a year, label date not used as the year, old paths equal new, banned-word scan of every endpoint's text and of the page strings.
- `test_forecast.py` (15): raw count tripling with total reporting is not flagged; sustained rise in share is flagged after 3 months, a single month is not; seasonal repeat and binomial noise are not flagged; baseline and model error always present; LSTM shown only when better; short series refused; training uses the first months only; stale/untrained states; monthly share from a database built to known counts.
- Full suite: 225 passed, 2 skipped. CI lint rules clean. Route snapshot changed only by the intended key renames and additions.

## Findings
- With one quarter loaded (2026Q2, 4 months by receipt date) `/api/lstm` correctly returns `no_data` ("at least 36 are needed"). The forecast and the LSTM can only be assessed on real data after the remaining quarters are loaded.
- The Weber and slope features use openFDA's live per-year counts, not the local data; moving them onto the local quarters is left to Phase 10 (coherence).

## Deviations
- `boxed_warning_dates.csv` starts empty: I could not verify a citable source for individual warning years in this session, and the plan allows an empty start.
- Forecast training cap raised from 50 to 200 epochs (small single-layer model, 24-36 sequences); still bounded per request.
