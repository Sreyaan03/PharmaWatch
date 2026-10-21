/* ============================================================
   prescription.js — Prescription Review view
   upload -> (extraction) -> confirm or correct -> report (+ PDF)
   The report cannot be reached without passing the confirm step: the
   server refuses (409) until a confirmed list exists. Research use only.
   ============================================================ */

const RX = { jobId: null, config: null, timer: null, started: false };

function rxEl(id) { return document.getElementById(id); }
function rxShow(step) {
  ['rx-step-upload', 'rx-step-progress', 'rx-step-confirm', 'rx-step-report'].forEach(s => {
    const el = rxEl(s);
    if (el) el.style.display = (s === step) ? 'block' : 'none';
  });
}
function rxStopPolling() { if (RX.timer) { clearTimeout(RX.timer); RX.timer = null; } }
function rxError(msg) {
  const box = rxEl('rx-error');
  if (!box) return;
  box.textContent = msg || '';
  box.style.display = msg ? 'block' : 'none';
}
function rxProgress(text) { const el = rxEl('rx-progress-text'); if (el) el.textContent = text || 'Working…'; }

async function initPrescription() {
  if (RX.started) return;
  RX.started = true;
  try {
    RX.config = await (await fetch('/api/prescription/config')).json();
    const n = rxEl('rx-extractor-notice');
    if (n) {
      n.textContent = RX.config.notice;
      n.style.borderLeftColor = RX.config.cloud ? '#e65100' : '#2e7d32';
    }
    const lim = rxEl('rx-limits');
    if (lim) lim.textContent = `Accepted: ${RX.config.allowed_types.join(', ').toUpperCase()} · up to ${RX.config.max_upload_mb} MB · ` +
      `the upload is kept ${RX.config.upload_kept} · results are erased after ${RX.config.retention_minutes} minutes or when you delete them.`;
  } catch (e) { rxError('Could not reach the server.'); }

  rxEl('rx-upload-btn')?.addEventListener('click', rxUpload);
  rxEl('rx-add-row')?.addEventListener('click', () => rxAddRow({}));
  rxEl('rx-confirm-btn')?.addEventListener('click', rxConfirm);
  rxEl('rx-restart-1')?.addEventListener('click', rxRestart);
  rxEl('rx-restart-2')?.addEventListener('click', rxRestart);
  rxEl('rx-delete-btn')?.addEventListener('click', rxDelete);
  rxShow('rx-step-upload');
}

function rxRestart() {
  rxStopPolling();
  RX.jobId = null;
  rxError('');
  const f = rxEl('rx-file'); if (f) f.value = '';
  rxShow('rx-step-upload');
}

async function rxDelete() {
  if (!RX.jobId) return rxRestart();
  try { await fetch(`/api/prescription/${RX.jobId}`, { method: 'DELETE' }); } catch (e) { /* the server expires it anyway */ }
  rxRestart();
}

/* ── step 1: upload ─────────────────────────────────────────── */
async function rxUpload() {
  rxError('');
  const file = rxEl('rx-file')?.files[0];
  if (!file) return rxError('Choose a file first.');
  const form = new FormData();
  form.append('file', file);
  form.append('reference', (rxEl('rx-reference')?.value || '').slice(0, 80));
  rxShow('rx-step-progress');
  rxProgress('Uploading…');
  try {
    const res = await fetch('/api/prescription', { method: 'POST', body: form });
    const body = await res.json();
    if (!res.ok) { rxShow('rx-step-upload'); return rxError(body.error || 'Upload failed.'); }
    RX.jobId = body.job_id;
    rxPollExtraction();
  } catch (e) { rxShow('rx-step-upload'); rxError('Could not reach the server.'); }
}

async function rxPollExtraction() {
  try {
    const res = await fetch(`/api/prescription/${RX.jobId}`);
    const body = await res.json();
    if (!res.ok) { rxShow('rx-step-upload'); return rxError(body.error || 'The job was not found.'); }
    if (body.status === 'failed') { rxShow('rx-step-upload'); return rxError(`Extraction failed: ${body.error}`); }
    if (body.status !== 'done') { rxProgress(body.progress || 'Reading the document…'); RX.timer = setTimeout(rxPollExtraction, 700); return; }
    rxRenderConfirm(body.extraction);
  } catch (e) { rxShow('rx-step-upload'); rxError('Lost contact with the server.'); }
}

/* ── step 2: confirm or correct ─────────────────────────────── */
function rxConfClass(c) { return c < 0.4 ? 'rx-conf rx-conf--low' : c < 0.6 ? 'rx-conf rx-conf--mid' : 'rx-conf rx-conf--ok'; }

function rxRenderConfirm(extraction) {
  const how = {
    local_text: 'Read from the PDF text layer on this machine.',
    local_ocr: 'Read with OCR on this machine; OCR can misread, so check every row.',
    cloud_vision: 'Read by a cloud service from the page images; check every row.',
  }[extraction.extractor] || 'Extracted automatically; check every row.';
  rxEl('rx-extractor-used').textContent = `${how} Pages: ${extraction.pages}.`;
  const unread = rxEl('rx-unreadable');
  unread.style.display = (extraction.unreadable_regions || []).length ? 'block' : 'none';
  unread.textContent = (extraction.unreadable_regions || []).length
    ? `Parts that could not be read well: ${extraction.unreadable_regions.join(' | ')}` : '';
  const body = rxEl('rx-rows');
  body.innerHTML = '';
  if (!extraction.lines.length) rxEl('rx-empty').style.display = 'block'; else rxEl('rx-empty').style.display = 'none';
  extraction.lines.forEach(line => rxAddRow(line));
  rxShow('rx-step-confirm');
}

function rxAddRow(line) {
  const tr = document.createElement('tr');
  const res = line.resolution || {};
  const ing = line.ingredient || (res.ingredients && res.ingredients.length === 1 ? res.ingredients[0] : '') || '';
  const unrecognised = line.drug_text && !ing && res.resolved === false;
  const conf = typeof line.confidence === 'number' ? line.confidence : 0;
  const val = (k) => escHtml(line[k] || '');
  tr.innerHTML = `
    <td><input class="rx-in" data-k="drug_text" value="${val('drug_text')}" aria-label="Drug as written"></td>
    <td><input class="rx-in ${unrecognised ? 'rx-in--warn' : ''}" data-k="ingredient" value="${escHtml(ing)}"
         placeholder="${unrecognised ? 'not recognised — type the ingredient' : 'ingredient'}" aria-label="Ingredient"></td>
    <td><input class="rx-in rx-in--s" data-k="strength" value="${val('strength')}"></td>
    <td><input class="rx-in rx-in--s" data-k="dose" value="${val('dose')}"></td>
    <td><input class="rx-in rx-in--s" data-k="route" value="${val('route')}"></td>
    <td><input class="rx-in" data-k="frequency" value="${val('frequency')}"></td>
    <td><input class="rx-in rx-in--s" data-k="duration" value="${val('duration')}"></td>
    <td style="text-align:center;"><input type="checkbox" data-k="as_needed" ${line.as_needed ? 'checked' : ''}></td>
    <td><select class="rx-in rx-in--s" data-k="status">
      ${['active', 'stopped', 'allergy'].map(s => `<option value="${s}" ${line.status === s ? 'selected' : ''}>${s}</option>`).join('')}</select></td>
    <td><span class="${rxConfClass(conf)}" title="${escHtml(line.notes || '')}">${Math.round(conf * 100)}%</span>
        ${line.notes ? `<div class="rx-note">${escHtml(line.notes)}</div>` : ''}</td>
    <td><button class="btn btn--sm btn--outline rx-del" title="Remove this row" aria-label="Remove row">✕</button></td>`;
  tr.dataset.confidence = String(conf);
  tr.dataset.notes = line.notes || '';
  tr.querySelector('.rx-del').addEventListener('click', () => tr.remove());
  rxEl('rx-rows').appendChild(tr);
}

function rxCollectRows() {
  return Array.from(rxEl('rx-rows').querySelectorAll('tr')).map(tr => {
    const get = (k) => tr.querySelector(`[data-k="${k}"]`);
    const text = (k) => (get(k).value || '').trim() || null;
    return {
      drug_text: (get('drug_text').value || '').trim(),
      ingredient: text('ingredient'), strength: text('strength'), dose: text('dose'), route: text('route'),
      frequency: text('frequency'), duration: text('duration'), as_needed: get('as_needed').checked,
      status: get('status').value, confidence: parseFloat(tr.dataset.confidence || '0'), notes: tr.dataset.notes || '',
    };
  }).filter(l => l.drug_text);
}

async function rxConfirm() {
  rxError('');
  const lines = rxCollectRows();
  if (!lines.length) return rxError('Add at least one medication before analysing.');
  rxShow('rx-step-progress');
  rxProgress('Checking interactions, warnings and signals…');
  try {
    const res = await fetch(`/api/prescription/${RX.jobId}/confirm`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ lines, reference: rxEl('rx-reference')?.value || '' }) });
    const body = await res.json();
    if (!res.ok) { rxShow('rx-step-confirm'); return rxError(body.error || 'Could not confirm the list.'); }
    rxPollReport();
  } catch (e) { rxShow('rx-step-confirm'); rxError('Could not reach the server.'); }
}

/* ── step 3: report ─────────────────────────────────────────── */
async function rxPollReport() {
  try {
    const res = await fetch(`/api/prescription/${RX.jobId}/report`);
    const body = await res.json();
    if (res.status === 202) { rxProgress(body.progress || 'Analysing…'); RX.timer = setTimeout(rxPollReport, 1200); return; }
    if (!res.ok) { rxShow('rx-step-confirm'); return rxError(body.error || 'The report could not be produced.'); }
    rxRenderReport(body);
  } catch (e) { rxShow('rx-step-confirm'); rxError('Lost contact with the server.'); }
}

const RX_EVIDENCE_LABEL = { label: 'FDA label', database: 'interaction database', measured: 'FAERS reports',
                            modelled: 'model prediction', documented: 'from the document' };

function rxRenderReport(r) {
  rxEl('rx-summary').textContent = r.summary;
  rxEl('rx-meds').innerHTML = r.medications.map(m => {
    const bits = [m.strength, m.dose, m.route, m.frequency, m.duration, m.as_needed ? 'as needed' : null].filter(Boolean);
    const status = m.status !== 'active' ? ` <em>[${escHtml(m.status)}]</em>` : '';
    return `<li><strong>${escHtml(m.drug_text)}</strong>${m.ingredient && m.ingredient.toLowerCase() !== (m.drug_text || '').toLowerCase()
      ? ` (${escHtml(m.ingredient)})` : ''} ${escHtml(bits.join(' · '))}${status}</li>`;
  }).join('');

  const groups = {};
  r.findings.forEach(f => { (groups[f.category_title] = groups[f.category_title] || []).push(f); });
  rxEl('rx-findings').innerHTML = r.findings.length ? Object.entries(groups).map(([title, items]) => `
    <h3 class="rx-h3">${escHtml(title)}</h3>
    ${items.map(f => `<div class="rx-finding rx-finding--${escHtml(f.category)}">
      <div class="rx-finding__title">${escHtml(f.title)}</div>
      <div class="rx-finding__detail">${escHtml(f.detail)}</div>
      <span class="rx-badge">${escHtml(RX_EVIDENCE_LABEL[f.evidence.type] || f.evidence.type)}</span>
      <span class="rx-badge rx-badge--src">${escHtml(f.evidence.source || '')}</span></div>`).join('')}`).join('')
    : '<p>No findings in the sources used. This is not a statement that the combination is acceptable.</p>';

  rxEl('rx-sections').innerHTML = (r.drug_sections || []).map(s => `
    <details class="rx-detail"><summary><strong>${escHtml(s.ingredient)}</strong>
      ${s.boxed_warning && s.boxed_warning.present ? '<span class="rx-badge rx-badge--warn">boxed warning</span>' : ''}</summary>
      ${s.boxed_warning && s.boxed_warning.present ? `<p class="rx-bw">${escHtml(s.boxed_warning.text)}</p>` : ''}
      ${s.label && s.label.pharm_class && s.label.pharm_class.length ? `<p>Class: ${escHtml(s.label.pharm_class.join(', '))}</p>` : ''}
      ${s.faers ? `<p>FAERS: ${Number(s.faers.exposed_cases).toLocaleString()} cases with this drug as suspect. Most reported:
        ${escHtml(s.faers.reactions.slice(0, 6).map(x => `${x.pt} (${x.cases.toLocaleString()})`).join(', '))}.</p>
        ${s.faers.signals.length ? `<p>Signals (reporting patterns, not causation): ${escHtml(s.faers.signals.slice(0, 5).map(x => `${x.pt} (PRR ${x.prr}, ${x.cases} cases)`).join('; '))}.</p>` : ''}`
        : '<p>No FAERS reports are indexed for this ingredient.</p>'}
    </details>`).join('');

  const TIER = { documented: 'documented', model_predicted: 'model-predicted (hypothesis)', no_evidence_found: 'no evidence found' };
  rxEl('rx-pairs').innerHTML = r.pairs.length ? `<table class="rx-table"><thead><tr><th>Pair</th><th>Result</th><th>Score</th></tr></thead><tbody>` +
    r.pairs.map(p => `<tr><td>${escHtml(p.drug_a)} + ${escHtml(p.drug_b)}</td><td>${escHtml(TIER[p.tier] || p.tier)}</td>
      <td>${p.tier === 'model_predicted' ? escHtml(String(p.interaction_likelihood_score)) + (p.calibrated ? '' : ' (uncalibrated)') : ''}</td></tr>`).join('') +
    '</tbody></table>' : '<p>Fewer than two medications were analysed.</p>';

  const graph = rxEl('rx-graph');
  graph.innerHTML = '';
  if (r.pairs.length && typeof drawPolypharmacyGraph === 'function' && typeof d3 !== 'undefined') {
    const names = Array.from(new Set(r.pairs.flatMap(p => [p.drug_a, p.drug_b])));
    const flagged = (p) => p.tier !== 'no_evidence_found';
    drawPolypharmacyGraph({
      nodes: names.map(n => ({ id: n, harmful_count: r.pairs.filter(p => flagged(p) && (p.drug_a === n || p.drug_b === n)).length })),
      links: r.pairs.map(p => ({ source: p.drug_a, target: p.drug_b, tier: p.tier, harmful: flagged(p),
        interaction_likelihood_score: p.interaction_likelihood_score })),
    }, graph, { showAll: true });
  }

  const na = [...(r.not_analysed || []).map(n => `Line ${n.line + 1} (${n.drug_text}): ${n.reason}`),
              ...(r.truncated || []).map(t => `${t}: beyond the limit on medications analysed per review`),
              ...(r.stopped || []).map(s => `${s.ingredient}: marked stopped, not analysed as active`),
              ...(r.allergies || []).map(a => `${a.ingredient}: recorded as an allergy`)];
  rxEl('rx-notanalysed').innerHTML = na.length ? na.map(x => `<li>${escHtml(x)}</li>`).join('') : '<li>All confirmed lines were analysed.</li>';
  rxEl('rx-limitations').innerHTML = r.limitations.map(l => `<li>${escHtml(l)}</li>`).join('');
  rxEl('rx-methods').textContent = r.methods;
  const s = r.sources;
  rxEl('rx-sources').textContent = `Extraction: ${s.extractor} (${s.text_source})${s.cloud_used ? ', used a cloud service' : ', processed on this machine'} · ` +
    `FAERS data: ${s.faers_data} · Interaction model: ${s.interaction_model || 'n/a'} · Generated ${r.generated_at}`;
  rxEl('rx-pdf').href = `/api/prescription/${RX.jobId}/report.pdf`;
  rxShow('rx-step-report');
}
