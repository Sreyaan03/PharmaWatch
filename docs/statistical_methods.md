# Statistical methods used by PharmaWatch

This is the reference for what the numbers mean. Every statement here is implemented in the code
named beside it and covered by a test. If this document and the code disagree, the code and its
tests are authoritative and this file is the bug.

## Data

- Source: FDA FAERS quarterly ASCII extracts (`backend/faers/`). Each case is de-duplicated: the
  version with the latest FDA date is kept, ties broken by the largest `primaryid`; cases named in
  the quarter's deleted-cases file are removed.
- Drug identity: the free-text product name is mapped to RxNorm ingredient(s) through RxNav (exact
  names only; a fuzzy match is offered as a suggestion, never used). Event identity: the MedDRA
  Preferred Term as reported, plus a short reviewed synonym table (`faers/event_synonyms.csv`).
- Exposure: a drug counts as exposure only when it is the primary or secondary suspect; a
  combination product requires every ingredient to be suspect in the same case.
- Every figure is a count of **reports**. Reports are not patients, not incidence, and not proof of
  causation. FAERS has no denominator of people exposed.

## The 2x2 table (`faers/stats.py: contingency`)

| | event | no event |
|---|---|---|
| drug | a | b |
| other drugs | c | d |

One function produces it, for every endpoint; strata (sex, age) restrict every cell to the stratum.

## Metrics (`faers/disproportionality.py: compute_metrics`)

- **PRR** = [a/(a+b)] / [c/(c+d)], 95% interval from the log-scale standard error
  sqrt(1/a − 1/(a+b) + 1/c − 1/(c+d)).
- **ROR** = ad/(bc), 95% interval from sqrt(1/a + 1/b + 1/c + 1/d).
- **Chi-square** with Yates continuity correction, on the observed table.
- **IC** (information component) = log2[(a + 0.5)/(E + 0.5)] with E = (a+b)(a+c)/N, the widely
  published approximation of the BCPNN IC. **IC025** is its lower 95% bound:
  IC − 3.3(a+0.5)^−1/2 − 2(a+0.5)^−3/2 (Gould 2003). This is not the full Bayesian BCPNN of the
  WHO Uppsala Monitoring Centre; it is the closed-form approximation.
- **Zero cells:** if any cell is zero, 0.5 is added to all four before PRR/ROR and their intervals
  (Haldane–Anscombe); the response sets `continuity_correction: true`.
- **Undefined tables** (no exposure, no event reports or no comparator) return zeros with
  `defined: false` and class `none`; they are never shown as a computed zero.

## Signal classes

| class | rule |
|---|---|
| `none` | the criteria below are not met |
| `signal` | a ≥ 3 and PRR ≥ 2 and chi-square ≥ 4 (the Evans criteria) |
| `strong_signal` | `signal`, and the ROR lower bound > 1, and IC025 > 0 |

The response lists each criterion's result under `criteria`. A class is a screening statement
about disproportionality in the reports, not a finding that the drug causes the event.

## Stratified analysis (`faers/stats.py: stratified_metrics`)

Sex and age groups each get their own 2x2 table and their own PRR with interval. A group is named
"higher risk" only when its PRR interval lies entirely above the other group's (all other groups'
for age). Fewer than 10 reports in a stratum gives no comparison. Cases with unknown sex or age
are reported as such, not assigned. Country figures are shares of reports and say where reports
are filed, not where risk is higher.

## Time to onset (`faers/stats.py: time_to_onset`)

Days from the earliest therapy start of the suspect drug to the event date, from the report's own
dates. A report is used only if both dates are complete (YYYYMMDD) and the event is not before the
start. The rest are counted by reason (`excluded`) and never estimated. No median or peak is given
below 10 usable reports.

## Temporal analysis

- **Velocity:** reports for the pair by month of FDA receipt over every month of the loaded window
  (zeros included), z-score against the series, spike if z > 2. Needs at least 12 months loaded.
- **Seasonality:** reports by calendar month of the event, using only calendar years the loaded
  window covers completely (a year must end at least 90 days before the newest receipt date to
  allow for reporting lag). Chi-square test against a spread proportional to days in the month,
  alpha 0.01, at least 2 such years and 60 reports. Months are named only when the test is
  significant.

## Boxed-warning features (`services/boxed_warnings.py`, `services/warning_rules.py`)

- A warning year is used only from `backend/data/boxed_warning_dates.csv` (with a source URL) or
  an explicit `warning_year` request parameter. The label's `effective_time` is a revision date
  and is reported as such. A drug with no boxed warning is never given a year.
- A reported event is matched to the warning text by the whole term as a phrase, or by a reviewed
  synonym. Matches record how (`term` or `synonym`).
- The output is a description of how reports are distributed over time. Report counts depend on
  reporting behaviour (publicity, litigation, regulatory attention) as well as use and harm, so
  they do not show that a warning worked, failed or was ignored.

## Forecast (`ml/forecast.py`)

The series is the drug's monthly share of all reports received (percent). Chronological split:
the last 12 months are the test window, the 12 before them calibrate the interval, and the LSTM is
fitted on everything earlier. Baseline: the same month a year earlier. The LSTM is shown only if
its mean absolute error on the test window is below the baseline's. Interval: 1.96 × the larger of
the validation RMSE and the binomial sampling noise of the month's report total. A run is
reported after 3 consecutive months above the interval, and describes reporting share, not risk.
At least 36 months are required (48 to train the LSTM).

## What the numbers do not show

Disproportionality is hypothesis-generating. It cannot show causation, incidence or relative risk
of the drug in patients; it is affected by stimulated reporting, confounding by indication,
masking by other drugs, duplicate and follow-up reports, and by what is and is not reported.
