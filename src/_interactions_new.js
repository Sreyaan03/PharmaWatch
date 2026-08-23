/* ──────────────── INTERACTIONS TAB ────────────────────────── */

/* ── Big Data status helpers ── */
let _bigdataReady = false;

async function checkBigdataStatus() {
  try {
    const r = await fetch('/api/system/bigdata-status');
    const d = await r.json();
    return d.hbase_ready === true;
  } catch { return false; }
}

function setBigdataUI(state, msg, sub) {
  const dot  = document.getElementById('bigdata-dot');
  const text = document.getElementById('bigdata-status-text');
  const subEl = document.getElementById('bigdata-status-sub');
  const btn  = document.getElementById('bigdata-retry-btn');
  if (!dot) return;
  dot.className = 'bigdata-status-bar__dot bigdata-status-bar__dot--' + state;
  if (text) text.childNodes[0].textContent = msg + ' ';
  if (subEl) subEl.textContent = sub || 'Apache HBase · Thrift port 9090';
  if (btn) btn.style.display = state === 'offline' ? 'inline-block' : 'none';
}

async function startBigdata() {
  setBigdataUI('starting', 'Starting HBase container…', 'docker start hbase-server');
  try {
    const r = await fetch('/api/system/start-bigdata', { method: 'POST' });
    const d = await r.json();
    if (d.status === 'ready') {
      _bigdataReady = true;
      setBigdataUI('ready', 'HBase Online', 'TWOSIDES dataset ready · 4.6M interactions');
    } else if (d.status === 'timeout') {
      setBigdataUI('offline', 'HBase starting — retry in a moment', d.message);
    } else {
      setBigdataUI('offline', 'Could not start HBase', d.message || '');
    }
  } catch (e) {
    setBigdataUI('offline', 'Backend unreachable', 'Make sure Flask is running on port 5000');
  }
}

async function initInteractionsTab() {
  // Wire retry button
  const retryBtn = document.getElementById('bigdata-retry-btn');
  if (retryBtn) retryBtn.addEventListener('click', startBigdata);

  // Check status first
  const ready = await checkBigdataStatus();
  if (ready) {
    _bigdataReady = true;
    setBigdataUI('ready', 'HBase Online', 'TWOSIDES dataset ready · 4.6M interactions');
  } else {
    await startBigdata();
  }

  // Wire tab switcher
  const btnExplorer = document.getElementById('tab-btn-explorer');
  const btnPoly     = document.getElementById('tab-btn-poly');
  const panelExplorer = document.getElementById('tab-explorer');
  const panelPoly     = document.getElementById('tab-poly');

  function switchTab(tab) {
    if (tab === 'explorer') {
      btnExplorer.classList.add('active');
      btnPoly.classList.remove('active');
      panelExplorer.style.display = 'block';
      panelPoly.style.display = 'none';
    } else {
      btnPoly.classList.add('active');
      btnExplorer.classList.remove('active');
      panelPoly.style.display = 'block';
      panelExplorer.style.display = 'none';
      loadRecentPredictions();
    }
  }

  if (btnExplorer) btnExplorer.addEventListener('click', () => switchTab('explorer'));
  if (btnPoly)     btnPoly.addEventListener('click',     () => switchTab('poly'));

  initExplorerTab();
  initPolypharmacyTab();
}

/* ── Explorer Tab ── */
function initExplorerTab() {
  const inputA = document.getElementById('explorer-drug-a');
  const inputB = document.getElementById('explorer-drug-b');
  const btn    = document.getElementById('explorer-search-btn');
  const suggsA = document.getElementById('explorer-suggestions-a');
  const suggsB = document.getElementById('explorer-suggestions-b');
  if (!inputA || !inputB) return;

  // Sync with global DrugContext
  if (typeof DrugContext !== 'undefined') {
    if (DrugContext.state.drug && !inputA.value) {
      inputA.value = DrugContext.state.drug;
    }
    DrugContext.on('change', (state) => {
      if (state.drug) {
        inputA.value = state.drug;
      }
    });
  }

  wireExplorerDrugInput(inputA, suggsA);
  wireExplorerDrugInput(inputB, suggsB);

  btn.addEventListener('click', () => {
    hideExplorerSuggestions();
    runExplorerPairCheck();
  });

  [inputA, inputB].forEach(input => {
    input.addEventListener('keydown', e => {
      if (e.key === 'Enter') { hideExplorerSuggestions(); runExplorerPairCheck(); }
      if (e.key === 'Escape') hideExplorerSuggestions();
    });
  });

  document.querySelectorAll('.explorer-chip').forEach(chip => {
    chip.addEventListener('click', () => {
      inputA.value = chip.dataset.drugA || '';
      inputB.value = chip.dataset.drugB || '';
      runExplorerPairCheck();
    });
  });

  document.addEventListener('click', e => {
    if (!e.target.closest('.explorer-drug-field')) hideExplorerSuggestions();
  });
}

function hideExplorerSuggestions() {
  document.querySelectorAll('.explorer-pair-suggestions').forEach(el => {
    el.style.display = 'none';
  });
}

function wireExplorerDrugInput(input, suggs) {
  let searchTimeout = null;
  input.addEventListener('input', () => {
    const q = input.value.trim();
    if (!q || q.length < 2) { suggs.style.display = 'none'; return; }
    clearTimeout(searchTimeout);
    suggs.innerHTML = '<li style="color:#888;padding:8px 12px;">Searching openFDA...</li>';
    suggs.style.display = 'block';
    searchTimeout = setTimeout(async () => {
      const matches = await ApiLayer.searchDrugNames(q);
      if (!matches.length) {
        suggs.innerHTML = `<li style="color:#888;padding:8px 12px;">No results for "${escHtml(q)}"</li>`;
      } else {
        suggs.innerHTML = matches.slice(0, 8).map(d =>
          `<li role="option" data-drug="${escHtml(d)}" style="padding:8px 12px;cursor:pointer;">${escHtml(d)}</li>`
        ).join('');
      }
      suggs.style.display = 'block';
    }, 280);
  });

  suggs.addEventListener('click', e => {
    const li = e.target.closest('li[data-drug]');
    if (!li) return;
    input.value = li.dataset.drug;
    suggs.style.display = 'none';
  });
}

async function runExplorerPairCheck() {
  const drugA = document.getElementById('explorer-drug-a')?.value.trim() || '';
  const drugB = document.getElementById('explorer-drug-b')?.value.trim() || '';
  const skeleton = document.getElementById('explorer-skeleton');
  const cards = document.getElementById('explorer-cards');
  const empty = document.getElementById('explorer-empty');

  if (cards) cards.style.display = 'none';
  if (!drugA && !drugB) {
    showExplorerEmpty('Select two drugs to check whether they are safe together',
      'The Explorer answers one focused question at a time: whether Drug A and Drug B have a predicted harmful interaction.');
    return;
  }
  if (!drugA || !drugB) {
    showExplorerEmpty('Add one more drug to run the pair check',
      'Choose both Drug A and Drug B before checking for an interaction.');
    return;
  }
  if (normalizeDrugName(drugA) === normalizeDrugName(drugB)) {
    showExplorerEmpty('Choose two different drugs',
      'The Explorer compares two distinct medications.');
    return;
  }

  empty.style.display = 'none';
  skeleton.style.display = 'flex';

  try {
    const res = await fetch('/api/graph/predict', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ drug_a: drugA, drug_b: drugB })
    });
    const data = await res.json();
    skeleton.style.display = 'none';

    if (!res.ok || data.error) {
      showExplorerEmpty('Could not complete the pair check',
        data.error || 'The backend returned an error while checking this drug pair.');
      return;
    }

    renderExplorerPairResult(data, cards);
    cards.style.display = 'flex';
  } catch (e) {
    skeleton.style.display = 'none';
    showExplorerEmpty('Could not reach backend', 'Make sure Flask is running on port 5000, then try the pair check again.');
  }
}

function showExplorerEmpty(title, desc) {
  const empty = document.getElementById('explorer-empty');
  const cards = document.getElementById('explorer-cards');
  if (cards) cards.style.display = 'none';
  if (!empty) return;
  empty.style.display = 'block';
  empty.querySelector('.empty-state__title').textContent = title;
  empty.querySelector('.empty-state__desc').textContent = desc;
}

function renderExplorerPairResult(data, container) {
  const harmful = data.is_harmful === true;
  const severity = getInteractionSeverity(data);
  const confidence = Number.isFinite(Number(data.confidence)) ? `${(Number(data.confidence) * 100).toFixed(0)}%` : 'N/A';
  const drugA = data.drug_a || 'Drug A';
  const drugB = data.drug_b || 'Drug B';
  const summary = harmful
    ? 'Predicted harmful interaction detected for this pair.'
    : 'No significant harmful interaction was predicted for this pair.';
  const action = harmful
    ? 'Avoid or review the combination, consider alternatives, and monitor closely if co-use is necessary.'
    : 'No known harmful signal in the current model output; continue normal clinical review.';
  const predicted = (data.predicted_interactions || []).join('; ') || data.prediction || summary;

  container.innerHTML = `
    <div class="interaction-card interaction-card--${harmful ? 'harmful' : 'safe'} interaction-card--detail">
      <div class="interaction-card__icon">${harmful ? '!' : 'OK'}</div>
      <div class="interaction-card__body">
        <div class="interaction-card__drugs">${escHtml(drugA)} + ${escHtml(drugB)}</div>
        <div class="interaction-card__effect">${escHtml(summary)}</div>
        <div class="interaction-card__details">
          <div><strong>Severity:</strong> ${escHtml(severity.label)}</div>
          <div><strong>Clinical concern:</strong> ${escHtml(predicted)}</div>
          <div><strong>Recommendation:</strong> ${escHtml(action)}</div>
          <div><strong>Evidence:</strong> ${escHtml(data.source || 'existing interaction model')} · Confidence ${escHtml(confidence)}</div>
        </div>
      </div>
      <span class="interaction-card__badge interaction-card__badge--${harmful ? 'harmful' : 'safe'}">${escHtml(severity.label)}</span>
    </div>
  `;
}

/* ── Polypharmacy Tab ── */
const MAX_SLOTS = 5;
let _polySlots = [];   // { el, resolved: {name,smiles,cid} | null }
let _lastPolyGraph = null;
let _lastPolyPairs = [];

function normalizeDrugName(name) {
  return String(name || '').trim().toLowerCase().replace(/\s+/g, ' ');
}

function getInteractionSeverity(item) {
  const harmful = item?.is_harmful === true;
  const confidence = Number(item?.confidence || 0);
  if (!harmful) return { key: 'safe', label: 'No known harmful interaction', color: '#69db7c', width: 1.5 };
  if (confidence >= 0.85) return { key: 'critical', label: 'High risk', color: '#c0392b', width: 4 };
  if (confidence >= 0.65) return { key: 'high', label: 'Moderate risk', color: '#ff6b6b', width: 3 };
  return { key: 'moderate', label: 'Possible risk', color: '#f08c00', width: 2.25 };
}

function initPolypharmacyTab() {
  const slotsContainer = document.getElementById('poly-drug-slots');
  const addBtn         = document.getElementById('poly-add-btn');
  const analyzeBtn     = document.getElementById('poly-analyze-btn');
  const safeToggle     = document.getElementById('poly-show-safe-toggle');
  if (!slotsContainer) return;

  // Init the two default slots
  _polySlots = [];
  slotsContainer.querySelectorAll('.drug-slot').forEach(el => {
    _polySlots.push({ el, resolved: null });
    wireSlot(_polySlots[_polySlots.length - 1]);
  });
  updatePolyUI();

  addBtn.addEventListener('click', () => {
    if (_polySlots.length >= MAX_SLOTS) return;
    const idx = _polySlots.length;
    const el = document.createElement('div');
    el.className = 'drug-slot';
    el.dataset.slot = idx;
    el.innerHTML = `
      <div class="drug-slot__num">${idx + 1}</div>
      <div class="drug-slot__input-wrap">
        <input type="text" class="drug-slot__input" placeholder="Drug name or SMILES…" autocomplete="off" />
        <ul class="drug-slot__suggestions" style="display:none;"></ul>
      </div>
      <span class="drug-slot__resolve drug-slot__resolve--pending">—</span>
      <button class="drug-slot__remove" title="Remove">✕</button>
    `;
    slotsContainer.appendChild(el);
    const slot = { el, resolved: null };
    _polySlots.push(slot);
    wireSlot(slot);
    updatePolyUI();
    el.querySelector('.drug-slot__input').focus();
  });

  analyzeBtn.addEventListener('click', runPolypharmacyAnalysis);
  if (safeToggle) {
    safeToggle.addEventListener('change', () => {
      const graphWrap = document.getElementById('poly-graph-wrap');
      const pairsEl = document.getElementById('poly-pairs');
      if (_lastPolyGraph && graphWrap && graphWrap.style.display !== 'none') {
        drawPolypharmacyGraph(_lastPolyGraph, graphWrap);
        renderPolyPairs(_lastPolyPairs, pairsEl);
      }
    });
  }
}

function wireSlot(slot) {
  const input  = slot.el.querySelector('.drug-slot__input');
  const suggs  = slot.el.querySelector('.drug-slot__suggestions');
  const badge  = slot.el.querySelector('.drug-slot__resolve');
  const removeBtn = slot.el.querySelector('.drug-slot__remove');
  let resolveTimer = null;
  let searchTimer  = null;

  removeBtn.addEventListener('click', () => {
    const idx = _polySlots.indexOf(slot);
    if (idx < 2) return;  // keep at least 2
    slot.el.remove();
    _polySlots.splice(idx, 1);
    // Renumber remaining slots
    _polySlots.forEach((s, i) => {
      s.el.dataset.slot = i;
      s.el.querySelector('.drug-slot__num').textContent = i + 1;
      s.el.querySelector('.drug-slot__remove').style.visibility = i < 2 ? 'hidden' : 'visible';
    });
    updatePolyUI();
  });

  input.addEventListener('input', () => {
    const q = input.value.trim();
    slot.resolved = null;
    badge.className = 'drug-slot__resolve drug-slot__resolve--pending';
    badge.textContent = '—';
    updatePolyUI();

    if (!q) { suggs.style.display = 'none'; return; }

    // Autocomplete
    clearTimeout(searchTimer);
    searchTimer = setTimeout(async () => {
      const matches = await ApiLayer.searchDrugNames(q);
      if (matches.length) {
        suggs.innerHTML = matches.slice(0, 6).map(d =>
          `<li data-drug="${escHtml(d)}">${escHtml(d)}</li>`
        ).join('');
        suggs.style.display = 'block';
      } else {
        suggs.style.display = 'none';
      }
    }, 250);

    // Resolve after 600ms pause
    clearTimeout(resolveTimer);
    resolveTimer = setTimeout(() => resolveSlot(slot, q, badge), 600);
  });

  suggs.addEventListener('click', e => {
    const li = e.target.closest('li[data-drug]');
    if (!li) return;
    input.value = li.dataset.drug;
    suggs.style.display = 'none';
    resolveSlot(slot, li.dataset.drug, badge);
  });

  input.addEventListener('keydown', e => {
    if (e.key === 'Escape') suggs.style.display = 'none';
  });

  document.addEventListener('click', e => {
    if (!slot.el.contains(e.target)) suggs.style.display = 'none';
  });
}

async function resolveSlot(slot, query, badge) {
  badge.className = 'drug-slot__resolve drug-slot__resolve--loading';
  badge.textContent = '…';
  try {
    const r = await fetch(`/api/interactions/resolve?input=${encodeURIComponent(query)}`);
    const d = await r.json();
    if (d.found) {
      slot.resolved = d;
      badge.className = 'drug-slot__resolve drug-slot__resolve--ok';
      badge.textContent = '✓ ' + escHtml(d.name);
    } else {
      slot.resolved = null;
      badge.className = 'drug-slot__resolve drug-slot__resolve--error';
      badge.textContent = '✗ Not found';
    }
  } catch {
    slot.resolved = null;
    badge.className = 'drug-slot__resolve drug-slot__resolve--error';
    badge.textContent = '✗ Error';
  }
  updatePolyUI();
}

function updatePolyUI() {
  const addBtn     = document.getElementById('poly-add-btn');
  const analyzeBtn = document.getElementById('poly-analyze-btn');
  const countEl    = document.getElementById('poly-slot-count');
  const n = _polySlots.length;
  const resolved = _polySlots.filter(s => s.resolved).length;
  if (addBtn)     addBtn.disabled = n >= MAX_SLOTS;
  if (analyzeBtn) analyzeBtn.disabled = resolved < 2;
  if (countEl)    countEl.textContent = `${n} / ${MAX_SLOTS} drugs · ${resolved} resolved`;
}

async function runPolypharmacyAnalysis() {
  const resolvedDrugs = _polySlots.filter(s => s.resolved).map(s => s.resolved.name);
  if (resolvedDrugs.length < 2) {
    renderPolyEmpty('Add at least two resolved drugs to build a medication safety map.');
    return;
  }

  const skeleton  = document.getElementById('poly-skeleton');
  const graphWrap = document.getElementById('poly-graph-wrap');
  const legend    = document.getElementById('poly-legend');
  const pairsEl   = document.getElementById('poly-pairs');

  pairsEl.style.display  = 'none';
  graphWrap.style.display = 'none';
  legend.style.display    = 'none';
  skeleton.style.display  = 'flex';

  try {
    const res  = await fetch('/api/interactions/polypharmacy', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ drugs: resolvedDrugs })
    });
    const data = await res.json();
    skeleton.style.display = 'none';

    if (data.error) {
      pairsEl.style.display = 'flex';
      pairsEl.innerHTML = `<p style="color:var(--cdc-red);padding:1rem;">❌ ${escHtml(data.error)}</p>`;
      return;
    }

    // Log backend errors so we can see if GNN failed on any pairs
    if (data.errors && data.errors.length) {
      console.warn('[Polypharmacy] Backend pair errors:', data.errors);
    }

    if (!data.graph || !data.graph.nodes || data.graph.nodes.length < 2) {
      pairsEl.style.display = 'flex';
      pairsEl.innerHTML = `<p style="color:var(--cdc-red);padding:1rem;">❌ Could not build graph — check that all drugs resolved correctly.</p>`;
      return;
    }

    _lastPolyGraph = normalizePolyGraph(data.graph, data.pairs || []);
    _lastPolyPairs = data.pairs || [];

    graphWrap.style.display = 'block';
    legend.style.display    = 'flex';
    // Allow browser to reflow so clientWidth is accurate before D3 reads it
    requestAnimationFrame(() => {
      drawPolypharmacyGraph(_lastPolyGraph, graphWrap);
    });
    renderPolyPairs(_lastPolyPairs, pairsEl);
    pairsEl.style.display = 'flex';
    loadRecentPredictions();

    // Fire the Uncharted Interactions panel asynchronously
    // (non-blocking — graph and pairs render first, then this fills in)
    const unchartedDrugs = resolvedDrugs.join(',');
    renderUnchartedPanel(unchartedDrugs).catch(() => {/* silent */});
  } catch (e) {
    skeleton.style.display = 'none';
    pairsEl.style.display  = 'flex';
    pairsEl.innerHTML = '<p style="color:var(--cdc-red);padding:1rem;">❌ Could not reach backend.</p>';
  }
}

function normalizePolyGraph(graph, pairs) {
  const nodes = (graph?.nodes || []).map(node => ({ ...node }));
  const pairByKey = new Map((pairs || []).map(pair => [
    makePairKey(pair.drug_a, pair.drug_b),
    pair
  ]));
  const links = (graph?.links || []).map(link => {
    const source = typeof link.source === 'object' ? link.source.id : link.source;
    const target = typeof link.target === 'object' ? link.target.id : link.target;
    const pair = pairByKey.get(makePairKey(source, target)) || {};
    return {
      ...link,
      source,
      target,
      is_harmful:   pair.is_harmful ?? link.is_harmful,
      confidence:   Number(pair.confidence ?? link.confidence ?? 0),
      side_effects: pair.side_effects ?? link.side_effects ?? [],
      pair_key:     makePairKey(source, target)
    };
  });
  return { nodes, links };
}

function makePairKey(a, b) {
  return [normalizeDrugName(a), normalizeDrugName(b)].sort().join('|');
}

function renderPolyGraphMessage(container, message) {
  container.innerHTML = `
    <div class="poly-graph-message">
      <div class="poly-graph-message__title">${escHtml(message)}</div>
    </div>
  `;
}

function renderPolyEmpty(message) {
  const graphWrap = document.getElementById('poly-graph-wrap');
  const legend = document.getElementById('poly-legend');
  const pairsEl = document.getElementById('poly-pairs');
  if (graphWrap) graphWrap.style.display = 'none';
  if (legend) legend.style.display = 'none';
  if (pairsEl) {
    pairsEl.style.display = 'flex';
    pairsEl.innerHTML = `<p style="color:var(--gray-600);padding:1rem;">${escHtml(message)}</p>`;
  }
}

function drawPolypharmacyGraph(graph, container) {
  const W = container.clientWidth || 700;
  const H = 380;
  container.innerHTML = '';
  const showSafePairs = document.getElementById('poly-show-safe-toggle')?.checked === true;
  const visibleLinks = (graph.links || []).filter(link => showSafePairs || link.is_harmful === true);
  const hasHarmfulLinks = (graph.links || []).some(link => link.is_harmful === true);

  if (!graph.nodes || graph.nodes.length === 0) {
    renderPolyGraphMessage(container, 'No medications selected.');
    return;
  }
  if (graph.nodes.length === 1) {
    renderPolyGraphMessage(container, 'Add at least one more medication to compare combinations.');
    return;
  }
  if (!showSafePairs && !hasHarmfulLinks) {
    // Don't disappear — draw graph nodes, show safe caption as SVG overlay
    // (early return removed: graph always renders)
  }

  const svg = d3.select(container).append('svg')
    .attr('width', W).attr('height', H)
    .style('background', '#0d1b2a').style('border-radius', '12px');

  // D3 zoom + pan
  const zoom = d3.zoom()
    .scaleExtent([0.4, 3])
    .on('zoom', (event) => g.attr('transform', event.transform));
  svg.call(zoom);

  // Reset zoom button
  const resetBtn = document.createElement('button');
  resetBtn.textContent = '\u27f3 Reset zoom';
  resetBtn.style.cssText = 'position:absolute;top:8px;right:8px;z-index:10;' +
    'background:rgba(255,255,255,0.1);color:#ccc;border:none;border-radius:4px;' +
    'padding:3px 8px;font-size:0.72rem;cursor:pointer;';
  resetBtn.addEventListener('click', () =>
    svg.transition().duration(400).call(zoom.transform, d3.zoomIdentity));
  container.style.position = 'relative';
  container.appendChild(resetBtn);

  // All graph content lives in this group (receives zoom transform)
  const g = svg.append('g');

  const sim = d3.forceSimulation(graph.nodes)
    .force('link', d3.forceLink(visibleLinks).id(d => d.id).distance(160))
    .force('charge', d3.forceManyBody().strength(-500))
    .force('center', d3.forceCenter(W / 2, H / 2))
    .force('collision', d3.forceCollide().radius(42));

  let tip = document.getElementById('_poly_tip');
  if (!tip) {
    tip = document.createElement('div');
    tip.id = '_poly_tip';
    tip.style.cssText = 'position:fixed;z-index:9999;background:#1a2a3a;color:#fff;padding:8px 12px;border-radius:8px;font-size:0.78rem;pointer-events:none;display:none;max-width:220px;line-height:1.5;box-shadow:0 4px 16px rgba(0,0,0,0.4);';
    document.body.appendChild(tip);
  }

  const link = g.append('g').selectAll('line').data(visibleLinks).join('line')
    .attr('stroke', d => getInteractionSeverity(d).color)
    .attr('stroke-opacity', d => d.is_harmful ? 0.85 : 0.45)
    .attr('stroke-width', d => getInteractionSeverity(d).width)
    .style('cursor', 'pointer')
    .on('mouseover', (event, d) => {
      const conf = (d.confidence * 100).toFixed(0);
      const severity = getInteractionSeverity(d);
      const se = (d.side_effects || []).slice(0, 2).join('; ');
      tip.innerHTML = `<strong>${escHtml(d.source.id || d.source)} + ${escHtml(d.target.id || d.target)}</strong><br>
        ${escHtml(severity.label)} · ${conf}% confidence${se ? `<br><em style="color:#a5d8ff">${escHtml(se)}</em>` : ''}`;
      tip.style.display = 'block';
    })
    .on('mousemove', event => {
      tip.style.left = (event.clientX + 14) + 'px';
      tip.style.top  = (event.clientY + 14) + 'px';
    })
    .on('mouseout', () => { tip.style.display = 'none'; });

  const node = g.append('g').selectAll('circle').data(graph.nodes).join('circle')
    .attr('r', d => 18 + (d.harmful_count || 0) * 4)
    .attr('fill', d => d.harmful_count > 0 ? '#ff6b6b' : '#69db7c')
    .attr('stroke', '#fff').attr('stroke-width', 2)
    .style('cursor', 'pointer')
    .on('mouseover', (event, d) => {
      tip.innerHTML = `<strong>${escHtml(d.id)}</strong><br>${d.harmful_count} harmful pair(s)`;
      tip.style.display = 'block';
    })
    .on('mousemove', event => {
      tip.style.left = (event.clientX + 14) + 'px';
      tip.style.top  = (event.clientY + 14) + 'px';
    })
    .on('mouseout', () => { tip.style.display = 'none'; })
    .call(d3.drag()
      .on('start', (event, d) => { if (!event.active) sim.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y; })
      .on('drag',  (event, d) => { d.fx = event.x; d.fy = event.y; })
      .on('end',   (event, d) => { if (!event.active) sim.alphaTarget(0); d.fx = null; d.fy = null; })
    );

  const labels = g.append('g').selectAll('text').data(graph.nodes).join('text')
    .text(d => d.id.length > 18 ? d.id.slice(0, 17) + '\u2026' : d.id)
    .attr('font-size', '11px').attr('font-weight', '500').attr('fill', '#e0e0e0')
    .attr('text-anchor', 'middle').attr('dy', 38)
    .style('pointer-events', 'none');

  // If all pairs are safe and toggle is off, show a calm overlay caption
  if (!showSafePairs && !hasHarmfulLinks) {
    svg.append('text')
      .attr('x', W / 2).attr('y', 20)
      .attr('text-anchor', 'middle')
      .attr('fill', '#69db7c')
      .attr('font-size', '12px')
      .attr('font-weight', '500')
      .text('\u2713 No harmful interactions in this combination \u2014 all pairs appear safe');
  }

  sim.on('tick', () => {
    link
      .attr('x1', d => (d.source && d.source.x != null) ? d.source.x : 0)
      .attr('y1', d => (d.source && d.source.y != null) ? d.source.y : 0)
      .attr('x2', d => (d.target && d.target.x != null) ? d.target.x : 0)
      .attr('y2', d => (d.target && d.target.y != null) ? d.target.y : 0);
    node.attr('cx', d => Math.max(24, Math.min(W - 24, d.x)))
        .attr('cy', d => Math.max(24, Math.min(H - 24, d.y)));
    labels.attr('x', d => Math.max(24, Math.min(W - 24, d.x)))
          .attr('y', d => Math.max(24, Math.min(H - 24, d.y)));
  });
}

function renderPolyPairs(pairs, container) {
  if (!container) return;
  const showSafePairs = document.getElementById('poly-show-safe-toggle')?.checked === true;
  const visiblePairs = (pairs || []).filter(p => showSafePairs || p.is_harmful === true);
  container.innerHTML = '';
  if (!pairs || !pairs.length) {
    container.innerHTML = '<p style="color:var(--gray-600);padding:1rem;">No pair results were returned.</p>';
    return;
  }
  if (!visiblePairs.length) {
    container.innerHTML = '<p style="color:var(--cdc-green);padding:1rem;">No harmful interactions found in this medication list.</p>';
    return;
  }
  container.innerHTML = visiblePairs.map(p => {
    const harmful  = p.is_harmful;
    const conf     = (p.confidence * 100).toFixed(0);
    const severity = getInteractionSeverity(p);
    const seText   = (p.side_effects || []).slice(0, 2).join(', ');
    const sourceBadge = getSourceBadgeHTML(p);
    const faersText = (p.faers_reports > 0)
      ? `<span class="pair-faers-count">${p.faers_reports.toLocaleString()} FAERS reports</span>`
      : '';
    const labelHtml = (p.label_documented && p.label_severity && p.label_severity !== 'unknown')
      ? `<span class="pair-label-sev pair-label-sev--${p.label_severity}">${escHtml(p.label_severity.charAt(0).toUpperCase() + p.label_severity.slice(1))}</span>`
      : '';
    const snippetHtml = (p.label_snippet)
      ? `<div class="pair-label-snippet">"…${escHtml(p.label_snippet.trim())}…"</div>`
      : '';
    return `
      <div class="poly-pair-item poly-pair-item--${harmful ? 'harmful' : 'safe'}">
        <div class="poly-pair-item__drugs">
          ${escHtml(p.drug_a)}
          <span class="poly-pair-item__arrow">&#x2194;</span>
          ${escHtml(p.drug_b)}
        </div>
        <div class="poly-pair-item__meta-row">
          ${sourceBadge}
          ${faersText}
          ${labelHtml}
          <span class="poly-pair-item__confidence">${conf}% GNN conf.</span>
        </div>
        <span class="poly-pair-item__badge poly-pair-item__badge--${harmful ? 'harmful' : 'safe'}">
          ${escHtml(severity.label)}
        </span>
        ${snippetHtml}
        ${seText ? `<div class="poly-pair-item__se">${escHtml(seText)}</div>` : ''}
      </div>`;
  }).join('');
}

async function loadRecentPredictions() {
  const container = document.getElementById('poly-recent');
  const list      = document.getElementById('poly-recent-list');
  if (!container || !list) return;
  try {
    const r = await fetch('/api/interactions/recent?limit=10');
    const d = await r.json();
    const preds = d.predictions || [];
    if (!preds.length) return;
    container.style.display = 'block';
    list.innerHTML = preds.map(p => {
      const when = p.queried_at ? p.queried_at.slice(0, 16).replace('T', ' ') : '';
      return `
        <div class="recent-pred-row">
          <span class="recent-pred-row__pair">
            ${escHtml(p.drug_a)} + ${escHtml(p.drug_b)}
          </span>
          <span class="poly-pair-item__badge poly-pair-item__badge--${p.is_harmful ? 'harmful' : 'safe'}" style="font-size:0.7rem;">
            ${p.is_harmful ? '&#x26a0; Harmful' : '&#x2713; Safe'}
          </span>
          <span class="recent-pred-row__time">${escHtml(when)}</span>
        </div>`;
    }).join('');
  } catch { /* silent */ }
}

/* ── Source badge helper ─────────────────────────────────────────── */
const SOURCE_META = {
  fda_label:    {
    label: 'FDA Documented',
    cls:   'src-fda',
    // Minimal SVG shield icon (no emoji)
    icon:  '<svg viewBox="0 0 16 16" width="12" height="12" fill="currentColor"><path d="M8 1l6 2.5v4C14 11 11.5 14 8 15 4.5 14 2 11 2 7.5v-4L8 1z"/></svg>'
  },
  faers_signal: {
    label: 'FAERS Signal',
    cls:   'src-faers',
    icon:  '<svg viewBox="0 0 16 16" width="12" height="12" fill="currentColor"><path d="M8 1a7 7 0 100 14A7 7 0 008 1zm0 3v5H6V4h2zm0 6.5a1 1 0 110 2 1 1 0 010-2z"/></svg>'
  },
  faers_weak:   {
    label: 'Weak Signal',
    cls:   'src-faers-weak',
    icon:  '<svg viewBox="0 0 16 16" width="12" height="12" fill="currentColor"><path d="M1 11L5 5l4 4 3-4 3 6H1z"/></svg>'
  },
  gnn_novel:    {
    label: 'GNN Novel',
    cls:   'src-gnn',
    icon:  '<svg viewBox="0 0 16 16" width="12" height="12" fill="currentColor"><circle cx="4" cy="4" r="2"/><circle cx="12" cy="4" r="2"/><circle cx="8" cy="12" r="2"/><path d="M4 4l4 8M12 4l-4 8M4 4h8"/></svg>'
  }
};

function getSourceBadgeHTML(pair) {
  const src = SOURCE_META[pair.data_source] || SOURCE_META.gnn_novel;
  return `<span class="pair-source-badge pair-source-badge--${src.cls}">${src.icon} ${escHtml(src.label)}</span>`;
}

/* ── Uncharted Interactions Panel ───────────────────────────────── */
async function renderUnchartedPanel(drugsCsv) {
  const panel = document.getElementById('uncharted-panel');
  if (!panel) return;

  // Show loading state immediately
  panel.style.display = 'block';
  panel.innerHTML = `
    <div class="uncharted-header">
      <div class="uncharted-header__title">
        <svg viewBox="0 0 20 20" width="16" height="16" fill="currentColor" style="vertical-align:-2px;margin-right:6px;opacity:0.8">
          <path d="M10 2a8 8 0 100 16A8 8 0 0010 2zm1 11H9v-2h2v2zm0-4H9V6h2v3z"/>
        </svg>
        Uncharted Interactions
      </div>
      <span class="uncharted-header__sub">Scanning FAERS for undocumented co-reporting signals…</span>
    </div>
    <div class="uncharted-loading">Querying FDA FAERS database…</div>
  `;

  try {
    const r   = await fetch(`/api/interactions/uncharted?drugs=${encodeURIComponent(drugsCsv)}&min_reports=50`);
    const d   = await r.json();
    const all = d.pairs || [];

    // All pairs for context, highlighted if uncharted
    const significant = all.filter(p => p.faers_reports >= 50);

    if (!significant.length) {
      panel.innerHTML = `
        <div class="uncharted-header">
          <div class="uncharted-header__title">Uncharted Interactions</div>
          <span class="uncharted-header__sub">FAERS surveillance (≥50 co-reports threshold)</span>
        </div>
        <p class="uncharted-empty">No pairs with significant FAERS co-reporting found at this threshold.<br>
          <span style="font-size:0.75rem;color:#888;">This may indicate genuinely low co-prescription or limited FAERS coverage for this combination.</span>
        </p>
      `;
      return;
    }

    const unchartedCount = d.uncharted_count || 0;
    const headerSub = unchartedCount > 0
      ? `${unchartedCount} undocumented signal${unchartedCount > 1 ? 's' : ''} detected — real-world reporting without FDA documentation`
      : 'All co-reported pairs have documented interactions';

    const rows = significant.map(p => {
      const isNew      = p.is_uncharted;
      const strCls     = { strong: 'sig-strong', moderate: 'sig-moderate', weak: 'sig-weak', none: 'sig-none' }[p.signal_strength] || 'sig-none';
      const srcLabel   = p.label_documented
        ? `<span class="uncharted-src uncharted-src--documented">FDA Label</span>`
        : p.in_twosides
          ? `<span class="uncharted-src uncharted-src--twosides">TWOSIDES</span>`
          : `<span class="uncharted-src uncharted-src--novel">Undocumented</span>`;

      const newBadge = isNew
        ? `<span class="uncharted-new-badge">NEW SIGNAL</span>`
        : '';

      return `
        <tr class="uncharted-row${isNew ? ' uncharted-row--highlight' : ''}">
          <td class="uncharted-td">${escHtml(p.drug_a)}</td>
          <td class="uncharted-td">${escHtml(p.drug_b)}</td>
          <td class="uncharted-td uncharted-td--num"><span class="sig-pill ${strCls}">${p.faers_reports.toLocaleString()}</span></td>
          <td class="uncharted-td">${srcLabel}${newBadge}</td>
        </tr>`;
    }).join('');

    panel.innerHTML = `
      <div class="uncharted-header">
        <div class="uncharted-header__title">
          <svg viewBox="0 0 20 20" width="16" height="16" fill="currentColor" style="vertical-align:-2px;margin-right:6px;opacity:0.8">
            <path d="M10 2a8 8 0 100 16A8 8 0 0010 2zm1 11H9v-2h2v2zm0-4H9V6h2v3z"/>
          </svg>
          Uncharted Interactions
        </div>
        <span class="uncharted-header__sub">${escHtml(headerSub)}</span>
      </div>
      <p class="uncharted-desc">
        Pairs below have real-world FAERS co-reporting but no documented interaction in FDA labels or TWOSIDES.
        High co-report counts on undocumented pairs are candidate emerging signals.
      </p>
      <table class="uncharted-table">
        <thead>
          <tr>
            <th class="uncharted-th">Drug A</th>
            <th class="uncharted-th">Drug B</th>
            <th class="uncharted-th">FAERS Reports</th>
            <th class="uncharted-th">Evidence</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
      <p class="uncharted-footnote">
        Source: FDA FAERS co-prescription query · Threshold: ≥50 reports ·
        <a href="https://www.fda.gov/drugs/questions-and-answers-fdas-adverse-event-reporting-system-faers/fda-adverse-event-reporting-system-faers-public-dashboard"
           target="_blank" rel="noopener" style="color:inherit;opacity:0.6;">About FAERS</a>
      </p>
    `;
  } catch (e) {
    panel.innerHTML = `
      <div class="uncharted-header"><div class="uncharted-header__title">Uncharted Interactions</div></div>
      <p class="uncharted-empty" style="color:#888;">Could not load FAERS signal data. Backend unreachable or rate-limited.</p>
    `;
  }
}
