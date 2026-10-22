# 11-01 Summary: Accounts, roles, audit log, feedback

Adds who-did-what and captures users' judgements as validation data, with the existing visual
style kept. Three commits: dc4a1fa (accounts/roles), 96cc6f2 (audit), c2655fd (feedback).

## Task 1 — Accounts and roles (done)
- `users` table; passwords are Werkzeug hashes, never stored or logged in clear text.
- `core/auth.py`: a replaceable `AuthProvider` (local-accounts `LocalAuthProvider`), signed session
  cookie via `FLASK_SECRET_KEY`, and `require_login` / `require_role` with ranks
  viewer < analyst < admin. Guards pass through when `AUTH_REQUIRED` is off (open local dev) and
  enforce when on; `require_admin` (X-Admin-Key) kept for the key-driven admin endpoints.
- `routes/auth.py`: `/api/auth/status | login | logout | me`. No self-registration.
- `core/manage.py` CLI: `create-user` / `set-role` / `deactivate` / `list-users` (password read
  interactively, stored hashed).
- Gated the "do work" POSTs to analyst+: `/api/ner`, `/api/signals/ner-mine`, `/api/prescription`.
- `app.py`: secret key (mandatory when `AUTH_REQUIRED` on), HTTP-only SameSite cookie, CORS with
  credentials. `src/auth.js`: in-style login overlay, "signed in as" chip, open-mode banner; any
  401 `{auth:"required"}` surfaces the login. One script tag added.
- Tests: hashing, login/me/bad-password, viewer blocked from an analyst action while an analyst is
  allowed (auth on), guards open when auth off.

## Task 2 — Audit log (done)
- `audit_log` table (user, action, route, method, inputs, data_version, model_version, status,
  request_id). The app DB user should hold INSERT/SELECT only; the code has **no UPDATE/DELETE**
  path on it (tested).
- Layering-clean split (the architecture test forced it): DB logic in `services/audit.py` (no
  Flask), admin blueprint in `routes/audit.py`, and the Flask request context read in `app.py`'s
  after-request hook which calls the service. Records analyses, uploads, confirmations, exports,
  login and admin actions; **never** records document content, free text or passwords (allow-listed
  inputs only). `request_id` per request.
- `routes/audit.py`: admin-only `GET /api/admin/audit` (paged) and `/export` (CSV).
- Tests: versions captured per route, login audited without the password, an analysis writes a row
  with the data version, a non-auditable request writes nothing, no UPDATE/DELETE on the table.

## Task 3 — Feedback (done, primary cards)
- `feedback` table (component, subject, verdict correct|incorrect|unsure, comment, data_version,
  model_version). `services/feedback.py`: submit, export_csv, counts. `routes/feedback.py`:
  `POST /api/feedback` (require_login), admin summary + CSV export.
- Frontend: `ApiLayer.renderFeedback(component, subject, meta)` — a thumbs up/down/unsure control
  beside the evidence badge, with one delegated handler. Attached to the signal detail and the
  drug-profile combined-evidence card. A submission carries the result's data/model versions, so a
  judgement is tied to the version it referred to.
- Tests: stored with versions, invalid verdict rejected, one CSV row per submission.

## Verified
- Per-task tests + the cross-cutting guards (architecture, route inventory, no-invented-numbers,
  frontend contract, auth, audit, feedback) all green. App boots; `/api/auth/status` works in open
  mode. Route inventory + provenance + `.env.example` updated for the new routes/settings.

## Limits / open points
- The feedback (and evidence-badge) control is on the **primary** result cards only; the full sweep
  across every card (temporal, demographics, dashboard tiles, prescription, text-mining) is the
  10-06 badge-sweep follow-up, folded into this plan and still pending.
- Role enforcement is only active with `AUTH_REQUIRED=on`; default dev stays open (by design, with a
  banner). A `FLASK_SECRET_KEY` is required to turn auth on.
- Working inside OneDrive resurrected a deleted file mid-task (`core/audit.py`); watch for this —
  Phase 11 docs should recommend cloning outside synced folders.
