/* ============================================================
   validation.js — Methods & Validation view
   Renders /api/validation/summary. Every number shown is read from that
   file; nothing here computes or supplies a metric.
   ============================================================ */

function validationEscape(text) {
  const el = document.createElement('div');
  el.textContent = String(text ?? '');
  return el.innerHTML;
}

function validationFormat(entry) {
  // entry = [value, [low, high] | null]
  const [value, ci] = entry;
  if (value === null || value === undefined) return '—';
  const v = Number(value).toFixed(3);
  return ci ? `${v} <span class="val-ci">[${Number(ci[0]).toFixed(3)}–${Number(ci[1]).toFixed(3)}]</span>` : v;
}

function renderValidationComponent(key, comp) {
  const metricNames = [];
  comp.rows.forEach(r => Object.keys(r.metrics).forEach(m => { if (!metricNames.includes(m)) metricNames.push(m); }));
  const head = `<tr><th>Set</th><th>Items</th>${metricNames.map(m => `<th>${validationEscape(m)}</th>`).join('')}</tr>`;
  const body = comp.rows.map(r => `<tr><td>${validationEscape(r.set)}</td><td>${r.pairs ?? '—'}</td>` +
    metricNames.map(m => `<td>${r.metrics[m] ? validationFormat(r.metrics[m]) : ''}</td>`).join('') + '</tr>').join('');
  const meta = [
    comp.reference ? `Reference: ${validationEscape(comp.reference)}` : '',
    comp.model ? `Model: ${validationEscape(comp.model)}` : '',
    comp.data_version ? `Data: ${validationEscape(comp.data_version)}` : '',
    comp.run_at ? `Run: ${validationEscape(comp.run_at)}` : '',
    comp.smoke ? 'Reduced (smoke) run' : '',
  ].filter(Boolean).join(' · ');
  return `
    <div class="dashboard-card" style="margin-top:1.25rem;" id="val-${key}">
      <div class="card-header"><h2 class="card-title">${validationEscape(comp.title)}</h2></div>
      <p style="font-size:0.8rem; color:#555; margin:0 0 0.75rem;">${meta}</p>
      ${comp.caveat ? `<p style="font-size:0.8rem; background:#fff8e1; border-left:3px solid #f9a825; padding:6px 10px; margin:0 0 0.75rem;">${validationEscape(comp.caveat)}</p>` : ''}
      <div style="overflow-x:auto;"><table class="val-table" style="width:100%; font-size:0.8rem; border-collapse:collapse;">
        <thead>${head}</thead><tbody>${body}</tbody></table></div>
    </div>`;
}

async function loadValidation() {
  const root = document.getElementById('validation-content');
  if (!root) return;
  root.innerHTML = '<p style="color:#888;">Loading recorded results…</p>';
  try {
    const res = await fetch('/api/validation/summary');
    const data = await res.json();
    if (data.error) { root.innerHTML = `<p style="color:#c0392b;">${validationEscape(data.error)}</p>`; return; }
    const keys = Object.keys(data.components || {});
    let html = '';
    if (keys.length === 0) {
      html += `<p style="color:#555;">${validationEscape(data.message || 'No validation results are recorded.')}</p>`;
    }
    keys.forEach(k => { html += renderValidationComponent(k, data.components[k]); });
    if ((data.not_run || []).length) {
      html += `<p style="font-size:0.8rem; color:#888; margin-top:1rem;">Not run in this summary: ${data.not_run.map(validationEscape).join(', ')}.</p>`;
    }
    const lim = data.known_limitations || [];
    if (lim.length) {
      html += `<div class="dashboard-card" style="margin-top:1.25rem;"><div class="card-header"><h2 class="card-title">Known limitations</h2></div>
        <ul style="font-size:0.85rem; line-height:1.6;">${lim.map(l => `<li>${validationEscape(l)}</li>`).join('')}</ul></div>`;
    }
    html += `<p style="font-size:0.78rem; color:#888; margin-top:1rem;">Summary generated ${validationEscape(data.generated_at || '')}. Formulas, thresholds and data rules: docs/METHODS.md.</p>`;
    root.innerHTML = html;
  } catch (err) {
    root.innerHTML = '<p style="color:#c0392b;">Could not load the validation summary.</p>';
  }
}
