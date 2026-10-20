/* Surveillance: the alert feed, review actions, and the drug watchlist.
   Alerts are possible signals for review, never findings. */
'use strict';

let surveillanceInited = false;

function svEsc(s) {
  return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

const SV_KIND_LABEL = { not_in_label: 'Not in label', new_signal: 'New signal', label_change: 'Label change' };

function initSurveillance() {
  if (!surveillanceInited) {
    surveillanceInited = true;
    document.getElementById('sv-apply').addEventListener('click', loadSurveillanceAlerts);
    document.getElementById('sv-watch-btn').addEventListener('click', svAddWatch);
    document.getElementById('sv-feed').addEventListener('click', svFeedClick);
    document.getElementById('sv-tab-live').addEventListener('click', () => svShowView('live'));
    document.getElementById('sv-tab-replay').addEventListener('click', () => svShowView('replay'));
    document.getElementById('sv-rp-start').addEventListener('click', svStartOrResume);
    document.getElementById('sv-rp-pause').addEventListener('click', svPauseReplay);
  }
  svLoadStatus();
  svLoadWatchlist();
  loadSurveillanceAlerts();
}

function svShowView(view) {
  const live = view === 'live';
  document.getElementById('sv-live').style.display = live ? '' : 'none';
  document.getElementById('sv-replay').style.display = live ? 'none' : '';
  document.getElementById('sv-tab-live').classList.toggle('sv-tab--active', live);
  document.getElementById('sv-tab-replay').classList.toggle('sv-tab--active', !live);
}

async function svLoadStatus() {
  const el = document.getElementById('sv-status');
  try {
    const r = await fetch('/api/surveillance/summary');
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || r.status);
    const c = d.counts || {};
    const total = Object.values(c).reduce((s, byStatus) => s + Object.values(byStatus).reduce((a, b) => a + b, 0), 0);
    const run = d.last_spark_run || {};
    el.innerHTML = `${total} alert(s). Latest batch: ${svEsc(run.latest_quarter || '—')} ` +
      `(${run.quarters || 0} quarters). Data: ${svEsc(d.data_version || '')}`;
  } catch (e) {
    el.textContent = 'Surveillance status unavailable (' + e.message + ').';
  }
}

async function svLoadWatchlist() {
  const el = document.getElementById('sv-watchlist');
  try {
    const d = await (await fetch('/api/surveillance/watchlist')).json();
    const drugs = d.drugs || [];
    el.innerHTML = drugs.length
      ? 'Watching: ' + drugs.map(x => `<span class="sv-chip">${svEsc(x.ingredient)} ` +
          `<a href="#" data-remove="${svEsc(x.rxcui)}" title="remove">&times;</a></span>`).join(' ')
      : 'No drugs on the watchlist. The strongest-signal drugs are watched automatically.';
  } catch (e) { el.textContent = ''; }
}

async function loadSurveillanceAlerts() {
  const feed = document.getElementById('sv-feed');
  feed.innerHTML = '<p style="color:#889;">Loading alerts…</p>';
  const params = new URLSearchParams();
  const kind = document.getElementById('sv-kind').value;
  const status = document.getElementById('sv-statusf').value;
  const drug = document.getElementById('sv-drug').value.trim();
  if (kind) params.set('kind', kind);
  if (status) params.set('status', status);
  if (drug) params.set('drug', drug);
  try {
    const r = await fetch('/api/surveillance/alerts?' + params.toString());
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || r.status);
    const alerts = d.alerts || [];
    if (!alerts.length) { feed.innerHTML = '<p style="color:#889;">No alerts match. Run the batch job and the label watcher to populate the feed.</p>'; return; }
    feed.innerHTML = alerts.map(svAlertHtml).join('');
  } catch (e) {
    feed.innerHTML = `<p style="color:#889;">Alerts unavailable (${svEsc(e.message)}). The stream and batch job may not have run.</p>`;
  }
}

function svAlertHtml(a) {
  const det = a.detail || {};
  const label = det.label || {};
  let body = '';
  if (a.kind === 'label_change') {
    const secs = det.changed_sections || {};
    body = Object.keys(secs).map(sec =>
      `<div class="sv-sentence"><strong>${svEsc(sec.replace(/_/g, ' '))}:</strong> ` +
      secs[sec].map(svEsc).join(' ') + '</div>').join('');
    body += `<div class="sv-meta">Label effective ${svEsc(det.effective_time || '')}` +
      (det.previous_effective_time ? ` (was ${svEsc(det.previous_effective_time)})` : '') + '</div>';
  } else {
    body = `<div class="sv-meta">IC025 ${a.ic025 != null ? a.ic025.toFixed(2) : '—'} · ` +
      `${a.cases || 0} cases · PRR ${det.prr != null ? det.prr : '—'} · became a signal in ` +
      `${svEsc(det.became_signal_in || a.quarter)} (not in ${svEsc(det.was_not_a_signal_in || '—')})</div>`;
    if (a.kind === 'not_in_label') {
      body += `<div class="sv-sentence">Not found in the FDA label text` +
        (label.generic_name ? ` for ${svEsc(label.generic_name)}` : '') +
        (label.label_effective_time ? ` (label ${svEsc(label.label_effective_time)})` : '') + '.</div>';
    } else if (label.status === 'in_label_text' && label.sentence) {
      body += `<div class="sv-sentence">In the label (${svEsc(label.section || '')}): “${svEsc(label.sentence)}”</div>`;
    }
    if (det.label_caught_up) {
      body += `<div class="sv-meta" style="color:#2980b9;">The label was later updated (${svEsc(det.label_caught_up.effective_time || '')}).</div>`;
    }
  }
  const title = a.kind === 'label_change'
    ? `${svEsc(a.ingredient)} — label updated`
    : `${svEsc(a.ingredient)} — ${svEsc(a.pt)}`;
  return `<div class="sv-alert sv-alert--${a.kind} sv-alert--${a.status}">
    <div><span class="sv-badge sv-badge--${a.kind}">${svEsc(SV_KIND_LABEL[a.kind] || a.kind)}</span>
      <strong>${title}</strong>
      ${a.status !== 'new' ? `<span class="sv-chip">${svEsc(a.status)}</span>` : ''}</div>
    ${body}
    <div class="sv-meta">${svEsc(det.note || '')}</div>
    <div class="sv-actions">
      ${a.kind !== 'label_change' ? `<a href="#signals" class="sv-chip" data-signal="${svEsc(a.ingredient)}|${svEsc(a.pt)}">see signal history →</a>` : ''}
      <button data-act="reviewed" data-id="${a.id}">Reviewed</button>
      <button data-act="escalated" data-id="${a.id}">Escalate</button>
      <button data-act="dismissed" data-id="${a.id}">Dismiss</button>
    </div></div>`;
}

async function svFeedClick(ev) {
  const btn = ev.target.closest('button[data-act]');
  if (btn) {
    ev.preventDefault();
    btn.disabled = true;
    try {
      const r = await fetch(`/api/surveillance/alerts/${btn.dataset.id}/status`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status: btn.dataset.act })
      });
      if (r.ok) loadSurveillanceAlerts(); else btn.disabled = false;
    } catch (e) { btn.disabled = false; }
    return;
  }
  const link = ev.target.closest('a[data-signal]');
  if (link) {
    ev.preventDefault();
    const [drug, event] = link.dataset.signal.split('|');
    if (typeof navigateTo === 'function') navigateTo('signals');
    if (typeof showSignalDetail === 'function') {
      showSignalDetail({ drug, event, prr: 0, ror: 0, bcpnn_ic: 0, reports: 0, a: 0, b: 0, c: 0, d: 0, sev: 'moderate' });
    }
  }
  const rm = ev.target.closest('a[data-remove]');
  if (rm) { ev.preventDefault(); svRemoveWatch(rm.dataset.remove); }
}

async function svAddWatch() {
  const input = document.getElementById('sv-watch-add');
  const drug = input.value.trim();
  if (!drug) return;
  try {
    const r = await fetch('/api/surveillance/watchlist', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ drug })
    });
    const d = await r.json();
    if (r.ok) { input.value = ''; svLoadWatchlist(); }
    else alert(d.error || 'Could not add that drug.');
  } catch (e) { /* ignore */ }
}

async function svRemoveWatch(rxcui) {
  try {
    await fetch('/api/surveillance/watchlist/' + encodeURIComponent(rxcui), { method: 'DELETE' });
    svLoadWatchlist();
  } catch (e) { /* ignore */ }
}

/* ── Replay: play the loaded quarters through the pipeline, quarter by quarter ── */
let svReplay = { runId: null, quarters: [], idx: -1, playing: false, counts: {} };

function svReplayButtons(playing) {
  document.getElementById('sv-rp-start').disabled = playing;
  document.getElementById('sv-rp-pause').disabled = !playing;
}

// One handler for the start button: resume a paused, unfinished run, else start fresh.
function svStartOrResume() {
  if (svReplay.runId && !svReplay.playing && svReplay.idx < svReplay.quarters.length - 1) {
    svReplay.playing = true;
    svReplayButtons(true);
    svReplayTick();
  } else {
    svStartReplay();
  }
}

async function svStartReplay() {
  svReplay.playing = false;                         // stop any prior run
  const feed = document.getElementById('sv-rp-feed');
  feed.innerHTML = '<p style="color:#889;">Starting replay…</p>';
  try {
    const r = await fetch('/api/surveillance/replay/start', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({})
    });
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || r.status);
    svReplay = { runId: d.run_id, quarters: d.quarters, idx: -1, playing: true, counts: {} };
    document.getElementById('sv-rp-start').textContent = '▶ Start replay';
    svRenderTimeline();
    feed.innerHTML = '';
    svReplayButtons(true);
    svReplayTick();
  } catch (e) {
    feed.innerHTML = `<p style="color:#889;">Could not start replay (${svEsc(e.message)}). The Spark batch job must have produced at least two quarters.</p>`;
  }
}

function svPauseReplay() {
  svReplay.playing = false;
  svReplayButtons(false);
  document.getElementById('sv-rp-start').textContent = '▶ Resume';
}

async function svReplayTick() {
  if (!svReplay.playing) return;
  svReplay.idx += 1;
  if (svReplay.idx >= svReplay.quarters.length) { svFinishReplay(); return; }
  const quarter = svReplay.quarters[svReplay.idx];
  svRenderTimeline();                               // mark current
  try {
    const r = await fetch(`/api/surveillance/replay/${svReplay.runId}/step`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ quarter })
    });
    const d = await r.json();
    if (r.ok) svReplay.counts[quarter] = d.by_kind || {};
  } catch (e) { /* keep going; a failed step shows as zero */ }
  svRenderTimeline();
  await svRenderReplayFeed(quarter);
  if (!svReplay.playing) return;
  if (svReplay.idx >= svReplay.quarters.length - 1) { svFinishReplay(); return; }
  const speed = parseInt(document.getElementById('sv-rp-speed').value, 10) || 2000;
  setTimeout(svReplayTick, speed);
}

function svFinishReplay() {
  svReplay.playing = false;
  svReplayButtons(false);
  document.getElementById('sv-rp-start').textContent = '▶ Replay again';
}

function svRenderTimeline() {
  const el = document.getElementById('sv-rp-timeline');
  el.innerHTML = svReplay.quarters.map((q, i) => {
    const c = svReplay.counts[q] || {};
    const n = (c.not_in_label || 0) + (c.new_signal || 0);
    const cls = i === svReplay.idx ? 'sv-qtile--current' : (i < svReplay.idx ? 'sv-qtile--done' : '');
    const shown = i <= svReplay.idx ? `<b>${n}</b>new` : '<b>·</b>&nbsp;';
    return `<div class="sv-qtile ${cls}">${svEsc(q)}${shown}</div>`;
  }).join('');
  const done = Math.max(0, svReplay.idx + 1);
  const cur = svReplay.quarters[svReplay.idx];
  document.getElementById('sv-rp-progress').textContent =
    svReplay.runId ? `Quarter ${Math.min(done, svReplay.quarters.length)} of ${svReplay.quarters.length}` +
      (cur ? ` — ${cur}` : '') : '';
}

async function svRenderReplayFeed(upto) {
  const feed = document.getElementById('sv-rp-feed');
  try {
    const r = await fetch(`/api/surveillance/replay/${svReplay.runId}?upto=${encodeURIComponent(upto)}&limit=100`);
    const d = await r.json();
    const alerts = d.alerts || [];
    if (!alerts.length) { feed.innerHTML = `<p style="color:#889;">No alerts yet through ${svEsc(upto)}.</p>`; return; }
    feed.innerHTML = `<p class="sv-meta">${alerts.length} alert(s) raised through ${svEsc(upto)}, strongest first.</p>` +
      alerts.map(svReplayAlertHtml).join('');
  } catch (e) { /* leave the last render in place */ }
}

function svReplayAlertHtml(a) {
  const det = a.detail || {};
  const label = det.label || {};
  let body = `<div class="sv-meta">IC025 ${a.ic025 != null ? a.ic025.toFixed(2) : '—'} · ` +
    `${a.cases || 0} cases · became a signal in ${svEsc(a.quarter)}</div>`;
  if (a.kind === 'not_in_label') {
    body += `<div class="sv-sentence">Not found in the FDA label text` +
      (label.generic_name ? ` for ${svEsc(label.generic_name)}` : '') + '.</div>';
  } else if (label.status === 'in_label_text' && label.sentence) {
    body += `<div class="sv-sentence">In the label (${svEsc(label.section || '')}): “${svEsc(label.sentence)}”</div>`;
  }
  return `<div class="sv-alert sv-alert--${a.kind}">
    <div><span class="sv-badge sv-badge--${a.kind}">${svEsc(SV_KIND_LABEL[a.kind] || a.kind)}</span>
      <strong>${svEsc(a.ingredient)} — ${svEsc(a.pt)}</strong></div>
    ${body}</div>`;
}

window.initSurveillance = initSurveillance;
