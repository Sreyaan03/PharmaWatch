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
  }
  svLoadStatus();
  svLoadWatchlist();
  loadSurveillanceAlerts();
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

window.initSurveillance = initSurveillance;
