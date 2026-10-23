# 11-03 Summary: Documentation & pilot release

The documents a college needs to install, run and evaluate the tool, plus the licence. The
visual app is unchanged (docs only). Owner decisions taken: licence **PolyForm Noncommercial
1.0.0**; student registration numbers **removed** from the README.

## Task 1 — README, licence, deployment guide (done)
- **README.md** rewritten to describe the app as it is: a pharmacovigilance signal-screening and
  teaching tool on real FAERS data. Removed the stale "all data is simulated", "no dependencies"
  and old structure listing. Added: what it is / is not, the seven areas, where the data comes
  from, a Docker quick start, the validation headline (DDI S1 AUROC 0.849 / AUPRC 0.851; lead-time
  19.4% flagged before, median 7 quarters, pending faculty review), accounts/roles, the research-
  use statement, and the new licence badge. Registration numbers removed.
- **LICENSE** added: PolyForm Noncommercial License 1.0.0 (full text, Required Notice naming the
  authors and repo). README badge and footer updated from MIT to this.
- **docs/DEPLOYMENT.md** (new): requirements, `.env` configuration table, first install (Docker and
  no-Docker), Compose profiles, MySQL + `DATA_DIR` backups, upgrade, server hosting (configuration
  only), the key-rotation release checkpoint, and health/troubleshooting.
- Folded **docs/README_SIMPLE.md** and **docs/how_to_run.txt** into short pointers to the README /
  DEPLOYMENT; rewrote **docker/README.md** for the single profile-based compose (done in 11-02);
  updated **docs/INDEX.md** to the current document set.

## Task 2 — Pilot guide and methods (done)
- **docs/PILOT_GUIDE.md** (new) for college staff: purpose and limits, roles and how accounts are
  issued (CLI, no self-registration), teaching exercises per area, how feedback becomes validation
  data, how faculty contribute annotated (synthetic) prescription samples, data-protection rules
  (synthetic prescriptions only until ethics approval), and how to report a problem.
- **docs/METHODS.md** updated: a lead-time-study section with the real counts
  (`reports/validation/lead_time.json`) framed as timing agreement and pending faculty review, and
  a "how to describe the tool" note fixing the wording to "benchmarked against published reference
  standards", never "medically validated".

## Verification done here
- Every module command cited in the docs imports/exists: `core.manage` (create-user), `db.migrate`,
  `core.config`, `faers.downloader/parser/loader`, `validation.run_all`.
- Numbers quoted are read from the repo (DDI benchmark table, `lead_time.json`), none invented.

## Task 3 — Fresh-clone release check (PENDING OWNER — human-verify checkpoint)
Not done here; it requires a human and owner decisions that are now made:
1. Clone into a new folder, follow `docs/DEPLOYMENT.md`, point `DATA_DIR` at the loaded data.
2. Work through `docs/UAT_CHECKLIST.md` as each role.
3. Run `python -m validation.run_all` and compare with the published page.
4. Decisions confirmed: licence = PolyForm Noncommercial 1.0.0; registration numbers removed.
5. Record results, mark the milestone complete, and tag the release.

STATE.md and ROADMAP.md updated to: Phase 11 built, release verification pending owner sign-off.

## Standing checkpoint still open
Rotate the committed FDA/NCBI API keys and purge them from git history **before any public
hosting**. Owner-skipped for now; recorded in STATE, ROADMAP, DEPLOYMENT and the 11-02 summary.
