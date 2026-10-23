# PharmaWatch — Pilot Guide for College Staff

This guide is for faculty and staff running PharmaWatch as a teaching and research tool. It assumes
no reading of the code. For installation and hosting, see [`DEPLOYMENT.md`](DEPLOYMENT.md); for the
formulas and limitations, see [`METHODS.md`](METHODS.md).

---

## 1. What the tool is for — and its limits

PharmaWatch helps students learn **pharmacovigilance signal screening** on real FDA FAERS data: how
disproportionality statistics work, what a "signal" does and does not mean, and how such screening
relates to later regulatory action.

**It is research and educational only.** Make these limits explicit to students:

- Every number is a count of **reports**, not patients, not incidence, not risk.
- A **signal** means a drug–event pair is *reported* more often than expected. It is **not** proof
  that the drug causes the event.
- The interaction model gives an **interaction-likelihood score** from molecular structure — a
  hypothesis, never a "probability of harm" or a "safety score."
- The absence of a result means **"no evidence found,"** never "safe to combine."
- Describe the tool as **"benchmarked against published reference standards,"** never "medically
  validated."

---

## 2. Roles and accounts

Roles are **viewer < analyst < admin**:

- **viewer** — browse results.
- **analyst** — run analyses, text mining and prescription extraction.
- **admin** — plus the audit log and feedback export.

There is **no self-registration.** An admin issues accounts from the server:

```bash
cd backend
python -m core.manage create-user     # prompts for username, role and password
python -m core.manage set-role <user> analyst
python -m core.manage list-users
```

Give each student their own account so feedback and audit records are attributable. Passwords are
stored only as salted hashes.

---

## 3. Suggested teaching exercises

| Area | Exercise |
|---|---|
| **Signal Detection** | Take a known pair (e.g. a watchlist drug) and read the 2×2 table, PRR, ROR and IC025. Ask: why is a signal not causation? What happens with a zero cell? |
| **Stratification** | Compare a signal by sex and age. When is one group "higher risk" — and why does an overlapping interval mean "no comparison"? |
| **Time to onset** | Discuss why ~19% of FAERS dates fall on 1 January and how the tool excludes imprecise dates. |
| **Drug Interactions** | Score a pair; read the calibration scope (trustworthy only for pairs with an unseen drug). Contrast a high score with "no clinical evidence." |
| **Replay** | Replay surveillance over past quarters and watch when a pair would first have flagged — then compare with the real FDA labeling-change date. |
| **Text mining** | Paste a case sentence (including a negated or fatal-outcome example) and inspect the extracted drug–event mentions. |
| **Methods & Validation** | Follow an evidence badge from any result to the method and the measured benchmark behind it. |

---

## 4. Giving feedback (this is the research contribution)

Beside each result is an evidence badge and a **feedback control** (correct / incorrect / unsure,
with an optional comment). Encourage students and faculty to use it: each judgement is stored with
the data and model version and becomes labelled validation data. Admins export it from
**/api/feedback/export**.

### Contributing annotated prescription samples

The prescription test set is built from annotated samples. Faculty can contribute: provide a
**synthetic** prescription image (see the data-protection rule below) together with the correct
drug/strength/frequency annotations, and hand them to the maintainers to add to the benchmark. The
benchmark is scored by `python -m validation.ner_benchmark` / the prescription validation run.

---

## 5. Data protection

- **Synthetic prescriptions only.** Until ethics approval is in place, do **not** upload real
  patient prescriptions or any identifiable patient data. Use made-up names and details.
- FAERS is already public, de-identified reporting data.
- Accounts, the audit log and feedback contain usernames and the actions taken; host the pilot on
  institutional infrastructure and restrict admin access.
- The audit log never records document content, free text or passwords — only allow-listed fields.

---

## 6. Reporting a problem

- Note what you did, what you expected and what happened, plus the `request_id` shown on any error.
- For a wrong or surprising *result*, use the feedback control (it captures the exact version).
- For a bug or outage, open an issue on the project repository, or contact the maintainers
  (Sreyaan Roy, Sohan Arvind Sanil, Manipal Academy of Higher Education).

---

## 7. Before you rely on the lead-time study

The lead-time result (how often the screen flags a pair before the FDA's labeling change) is
**author-built and pending faculty review.** Present it as a *timing-agreement* measure against
later FDA action, not as evidence the tool detects novel harms. The method and numbers are in
`METHODS.md` and `/api/validation/summary`.
