---
phase: 2
plan: 01
type: fix
wave: 1
depends_on: []
files_modified:
  - src/_interactions_new.js
  - src/app.js
  - src/ml_models.js
autonomous: true
requirements:
  - HARD-01
  - HARD-02
  - HARD-03
---

# Plan 01 — Replace All Hardcoded 127.0.0.1 URLs with Relative Paths

<objective>
Every `fetch('http://127.0.0.1:5000/api/...')` call is a latent deployment bug: if the app
is accessed from any hostname other than localhost (e.g., a LAN IP, a VM, or a deployed URL),
those fetches will silently fail because they hardcode the loopback address. The fix is trivial:
replace every absolute URL with a relative path (`/api/...`). The browser resolves relative
fetch URLs against `window.location.origin`, which is always correct.

Affected files:
- `src/_interactions_new.js` — 7 occurrences
- `src/app.js` — 11 occurrences
- `src/ml_models.js` — 3 occurrences
Total: 21 occurrences to fix
</objective>

<tasks>

## Task 1 — Fix _interactions_new.js (7 occurrences)

<read_first>
- src/_interactions_new.js (full file — lines 8, 29, 186, 391, 439, 718, 790)
</read_first>

<action>
In `src/_interactions_new.js`, replace ALL occurrences of the absolute URL prefix with an empty string (making paths relative). The 7 specific replacements:

Line 8:
```
BEFORE: const r = await fetch('http://127.0.0.1:5000/api/system/bigdata-status');
AFTER:  const r = await fetch('/api/system/bigdata-status');
```

Line 29:
```
BEFORE: const r = await fetch('http://127.0.0.1:5000/api/system/start-bigdata', { method: 'POST' });
AFTER:  const r = await fetch('/api/system/start-bigdata', { method: 'POST' });
```

Line 186:
```
BEFORE: const res = await fetch('http://127.0.0.1:5000/api/graph/predict', {
AFTER:  const res = await fetch('/api/graph/predict', {
```

Line 391:
```
BEFORE: const r = await fetch(`http://127.0.0.1:5000/api/interactions/resolve?input=${encodeURIComponent(query)}`);
AFTER:  const r = await fetch(`/api/interactions/resolve?input=${encodeURIComponent(query)}`);
```

Line 439:
```
BEFORE: const res  = await fetch('http://127.0.0.1:5000/api/interactions/polypharmacy', {
AFTER:  const res  = await fetch('/api/interactions/polypharmacy', {
```

Line 718:
```
BEFORE: const r = await fetch('http://127.0.0.1:5000/api/interactions/recent?limit=10');
AFTER:  const r = await fetch('/api/interactions/recent?limit=10');
```

Line 790:
```
BEFORE: const r   = await fetch(`http://127.0.0.1:5000/api/interactions/uncharted?drugs=${encodeURIComponent(drugsCsv)}&min_reports=50`);
AFTER:  const r   = await fetch(`/api/interactions/uncharted?drugs=${encodeURIComponent(drugsCsv)}&min_reports=50`);
```

Use a global search-and-replace for the string `'http://127.0.0.1:5000` → `'` (remove the host prefix from single-quote strings) and `` `http://127.0.0.1:5000 `` → `` ` `` (for template literals). Verify all 7 are removed.
</action>

<acceptance_criteria>
- `grep -c "127.0.0.1" src/_interactions_new.js` outputs `0`
- `grep "/api/system/bigdata-status" src/_interactions_new.js` outputs a match (relative path preserved)
- `grep "/api/graph/predict" src/_interactions_new.js` outputs a match
- `grep "/api/interactions/polypharmacy" src/_interactions_new.js` outputs a match
- `grep "/api/interactions/uncharted" src/_interactions_new.js` outputs a match
</acceptance_criteria>

---

## Task 2 — Fix app.js (11 occurrences)

<read_first>
- src/app.js (lines 523, 525, 814, 892, 1423, 1424, 1425, 1580, 1581, 1582, 2004)
</read_first>

<action>
In `src/app.js`, replace ALL occurrences of `http://127.0.0.1:5000` with `` (empty string). The 11 specific replacements:

Line 523:
```
BEFORE: fetch(`http://127.0.0.1:5000/api/trials/${encodeURIComponent(currentName)}`)
AFTER:  fetch(`/api/trials/${encodeURIComponent(currentName)}`)
```

Line 525:
```
BEFORE: fetch(`http://127.0.0.1:5000/api/prr-trials?drug=${encodeURIComponent(currentName)}&event=Nausea`)
AFTER:  fetch(`/api/prr-trials?drug=${encodeURIComponent(currentName)}&event=Nausea`)
```

Line 814:
```
BEFORE: const res = await fetch('http://127.0.0.1:5000/api/lstm?drug=Metformin');
AFTER:  const res = await fetch('/api/lstm?drug=Metformin');
```

Line 892:
```
BEFORE: const res = await fetch(`http://127.0.0.1:5000/api/prr-trials?drug=...`);
AFTER:  const res = await fetch(`/api/prr-trials?drug=...`);
```

Lines 1423, 1424, 1425 (Promise.all block):
```
BEFORE: fetch(`http://127.0.0.1:5000/api/boxed-warning/${encodeURIComponent(drugName)}`).then(r => r.json()),
        fetch(`http://127.0.0.1:5000/api/boxed-warning-events/${encodeURIComponent(drugName)}`).then(r => r.json()),
        fetch(`http://127.0.0.1:5000/api/trials/${encodeURIComponent(drugName)}`).then(r => r.json()).catch(...)
AFTER:  fetch(`/api/boxed-warning/${encodeURIComponent(drugName)}`).then(r => r.json()),
        fetch(`/api/boxed-warning-events/${encodeURIComponent(drugName)}`).then(r => r.json()),
        fetch(`/api/trials/${encodeURIComponent(drugName)}`).then(r => r.json()).catch(...)
```

Lines 1580, 1581, 1582:
```
BEFORE: fetch(`http://127.0.0.1:5000/api/boxed-warning/timeline/${encodeURIComponent(drug)}`).then...
        fetch(`http://127.0.0.1:5000/api/boxed-warning/violations/${encodeURIComponent(drug)}`).then...
        fetch(`http://127.0.0.1:5000/api/boxed-warning/bias-analysis/${encodeURIComponent(drug)}`).then...
AFTER:  fetch(`/api/boxed-warning/timeline/${encodeURIComponent(drug)}`).then...
        fetch(`/api/boxed-warning/violations/${encodeURIComponent(drug)}`).then...
        fetch(`/api/boxed-warning/bias-analysis/${encodeURIComponent(drug)}`).then...
```

Line 2004:
```
BEFORE: const res = await fetch('http://127.0.0.1:5000/api/boxed-warning/violations/all?limit=100').then(r => r.json());
AFTER:  const res = await fetch('/api/boxed-warning/violations/all?limit=100').then(r => r.json());
```
</action>

<acceptance_criteria>
- `grep -c "127.0.0.1" src/app.js` outputs `0`
- `grep "/api/trials/" src/app.js` has at least 2 matches (relative paths preserved)
- `grep "/api/boxed-warning/" src/app.js` has at least 3 matches
- `grep "/api/lstm" src/app.js` has at least 1 match
</acceptance_criteria>

---

## Task 3 — Fix ml_models.js (3 occurrences)

<read_first>
- src/ml_models.js (lines 65, 170, 304)
</read_first>

<action>
In `src/ml_models.js`, replace ALL 3 occurrences of `http://127.0.0.1:5000`:

Line 65:
```
BEFORE: const res = await fetch(`http://127.0.0.1:5000/api/lstm?drug=${encodeURIComponent(drug)}`);
AFTER:  const res = await fetch(`/api/lstm?drug=${encodeURIComponent(drug)}`);
```

Line 170:
```
BEFORE: const gapRes = await fetch(`http://127.0.0.1:5000/api/lstm/gap-scan?drug=${encodeURIComponent(drug)}`);
AFTER:  const gapRes = await fetch(`/api/lstm/gap-scan?drug=${encodeURIComponent(drug)}`);
```

Line 304:
```
BEFORE: `http://127.0.0.1:5000/api/lstm/train?drug=${encodeURIComponent(currentDrug)}`,
AFTER:  `/api/lstm/train?drug=${encodeURIComponent(currentDrug)}`,
```
</action>

<acceptance_criteria>
- `grep -c "127.0.0.1" src/ml_models.js` outputs `0`
- `grep "/api/lstm" src/ml_models.js` has at least 3 matches (relative paths preserved)
- `grep "/api/lstm/gap-scan" src/ml_models.js` has at least 1 match
- `grep "/api/lstm/train" src/ml_models.js` has at least 1 match
</acceptance_criteria>

</tasks>

<verification>
1. After all replacements, run:
   `grep -r "127.0.0.1" src/`
   Expected: no output (zero matches)

2. Spot-check that the app still functions by opening the browser and:
   - Loading a drug profile (triggers /api/trials and /api/prr-trials)
   - Clicking a signal row (triggers signal network graph via /api/signals/network)
   - Loading Interactions tab (triggers /api/system/bigdata-status)

3. Check browser DevTools Network tab — all API calls should go to the same origin (relative paths), not to 127.0.0.1.
</verification>

<must_haves>
- Zero occurrences of `127.0.0.1` in any file under src/
- All relative paths correctly formed (start with `/api/` not `api/`)
- Template literal URLs use backtick-relative paths: `` `/api/...` `` not `` `http://127.0.0.1:5000/api/...` ``
- No accidental breakage of existing query parameters (preserve `?drug=...&event=...` etc.)
</must_haves>
