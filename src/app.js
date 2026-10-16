/* ============================================================
   PHARMAWATCH — Application JavaScript
   Live API integrated architecture
   ============================================================ */

'use strict';

/* ──────────────── GLOBAL DRUG CONTEXT ──────────────────────── */
const DrugContext = {
  state: {
    drug: '',
    label: null,
    totalReports: 0,
    adeList: [],
    prr: null
  },
  listeners: [],
  on(event, cb) {
    if (event === 'change') {
      this.listeners.push(cb);
    }
  },
  set(newState) {
    this.state = { ...this.state, ...newState };
    this.listeners.forEach(cb => {
      try { cb(this.state); } catch (e) { console.error("Error in DrugContext listener:", e); }
    });
  }
};
window.DrugContext = DrugContext;

/* ──────────────── DATA STORE ──────────────────────────────── */
const DRUGS = [
  'Metformin', 'Atorvastatin', 'Lisinopril', 'Amoxicillin', 'Ibuprofen',
  'Warfarin', 'Amiodarone', 'Metoprolol', 'Amlodipine', 'Omeprazole'
];

const ADE_EVENTS = [
  'Nausea', 'Hepatotoxicity', 'Anaphylaxis', 'Rhabdomyolysis', 'Dizziness'
];

/* Signal classes returned by the backend (see backend/faers/disproportionality.py):
   strong_signal  3+ cases, PRR >= 2, chi-square >= 4, and ROR lower bound > 1 and IC025 > 0
   signal         3+ cases, PRR >= 2, chi-square >= 4
   none           the criteria are not met
   unavailable    no counts: the drug or event could not be resolved or is not indexed */
const SIGNAL_CLASS_META = {
  strong_signal: { label: 'STRONG SIGNAL', prr: 'prr-critical', pill: 'active', alert: 'critical', icon: '🚨' },
  signal:        { label: 'SIGNAL',        prr: 'prr-high',     pill: 'review', alert: 'high',     icon: '⚠️' },
  none:          { label: 'NO SIGNAL',     prr: 'prr-moderate', pill: 'closed', alert: 'moderate', icon: 'ℹ️' },
  unavailable:   { label: 'NOT AVAILABLE', prr: 'prr-moderate', pill: 'closed', alert: 'moderate', icon: 'ℹ️' }
};

/* Rows cached before the classes were renamed carry the old labels; "moderate" meant no signal. */
function normalizeSignalClass(value) {
  const legacy = { critical: 'strong_signal', high: 'signal', moderate: 'none' };
  const key = String(value || 'none').toLowerCase();
  if (SIGNAL_CLASS_META[key]) return key;
  return legacy[key] || 'none';
}

/* ──────────────── INTERACTIONS (replaced with live data) ──── */

/* ──────────────── TOP REPORTED EVENTS (live) ─────────────── */
async function loadTopEvents() {
  const list = document.getElementById('top-events-list');
  const note = document.getElementById('top-events-note');
  if (!list) return;
  try {
    const res = await fetch('/api/dashboard/top-events?limit=7');
    const data = await res.json();
    if (!data.events || data.events.length === 0) {
      list.innerHTML = '<li><span class="event-name">No FAERS data is loaded</span><span class="event-count"></span></li>';
      if (note) note.textContent = '';
      return;
    }
    list.innerHTML = data.events.map(e =>
      `<li><span class="event-name">${escHtml(e.event)}</span><span class="event-count">${e.reports.toLocaleString()}</span></li>`
    ).join('');
    if (note) {
      note.textContent = `Cases reporting each term · ${data.data_version}. Excludes product-use terms: ` +
        `${data.excluded_terms.join(', ')}.`;
    }
  } catch (err) {
    list.innerHTML = '<li><span class="event-name">Could not load top events</span><span class="event-count"></span></li>';
  }
}

/* ──────────────── SIGNALS TABLE ──────────────────────────── */
let signalsData = [];
let gaugePRRChart = null;
let gaugeRORChart = null;
let gaugeICChart = null;

async function fetchAndRenderSignals(forceRefresh = false) {
  const tbody = document.getElementById('signals-tbody');
  if (tbody && signalsData.length === 0) {
    tbody.innerHTML = `<tr><td colspan="9" style="text-align:center; padding:2rem; color:var(--text-muted);"><span style="animation: spin 1s linear infinite; display:inline-block; margin-right:8px;">⏳</span>Loading safety signals...</td></tr>`;
  }
  try {
    const url = forceRefresh ? '/api/signals?refresh=true' : '/api/signals';
    const res = await fetch(url);
    const json = await res.json();
    if (json && json.signals) {
      signalsData = json.signals.map((s, idx) => ({
        rank: idx + 1,
        drug: s.drug,
        event: s.event,
        prr: s.prr,
        ror: s.ror,
        bcpnn_ic: s.bcpnn_ic,
        reports: s.n_reports,
        source: s.source,
        sev: (s.unresolved && s.unresolved.length) ? 'unavailable' : normalizeSignalClass(s.signal_strength),
        note: s.note || '',
        a: s.a, b: s.b, c: s.c, d: s.d
      }));
      filterSignals();
      initPRRDistributionChart();
    }
  } catch (err) {
    console.error("Error fetching signals:", err);
    if (tbody) {
      tbody.innerHTML = `<tr><td colspan="9" style="text-align:center; padding:2rem; color:#ef4444;">⚠️ Failed to retrieve active signals. Check backend connection.</td></tr>`;
    }
  }
}

function renderSignalsTable(data) {
  const tbody = document.getElementById('signals-tbody');
  if (!tbody) return;
  
  if (data.length === 0) {
    tbody.innerHTML = `<tr><td colspan="9" style="text-align:center; padding:2rem; color:var(--text-muted);">No safety signals found matching filters.</td></tr>`;
    return;
  }
  
  tbody.innerHTML = data.map(s => {
    // Style chip for source
    let srcClass = s.source;
    let srcLabel = String(s.source).toUpperCase();
    if (s.source === 'faers' || s.source === 'openfda') {
      srcClass = 'fda';
      srcLabel = 'openFDA API';
    } else if (s.source === 'faers_local') {
      srcClass = 'faers-local';
      srcLabel = 'FAERS Local';
    } else if (s.source === 'ner_clinical') {
      srcClass = 'ehr';
      srcLabel = 'Clinical NER';
    }
    
    // Clean formatting for disproportionality metrics
    const unavailable = s.sev === 'unavailable';
    const meta = SIGNAL_CLASS_META[s.sev] || SIGNAL_CLASS_META.none;
    const prrDisplay = unavailable ? '—' : (s.prr !== undefined ? s.prr.toFixed(2) : 'N/A');
    const rorDisplay = unavailable ? '—' : (s.ror !== undefined ? s.ror.toFixed(2) : 'N/A');

    return `
      <tr data-rank="${s.rank}" class="signal-row" style="cursor: pointer;">
        <td><strong>${s.rank}</strong></td>
        <td><strong>${escHtml(s.drug)}</strong></td>
        <td><span class="illness-hover" data-illness="${escHtml(s.event)}" style="cursor:help; border-bottom:1px dashed #bbb;">${escHtml(s.event)}</span></td>
        <td class="${meta.prr}">${prrDisplay}</td>
        <td>${rorDisplay}</td>
        <td>${unavailable ? '—' : (s.bcpnn_ic !== undefined ? s.bcpnn_ic.toFixed(2) : 'N/A')}</td>
        <td>${unavailable ? '—' : (s.reports || 0).toLocaleString()}</td>
        <td><span class="source-chip source-chip--${srcClass}">${srcLabel}</span></td>
        <td><span class="status-pill status-pill--${meta.pill}" title="${escHtml(s.note)}">${meta.label}</span></td>
      </tr>
    `;
  }).join('');

  tbody.querySelectorAll('.signal-row').forEach(row => {
    row.addEventListener('click', () => {
      const selected = signalsData.find(s => s.rank == row.dataset.rank);
      if (selected) showSignalDetail(selected);
    });
  });

  if (typeof ApiLayer !== 'undefined') ApiLayer.initTooltips();
}

function renderGauge(canvasId, value, minVal, maxVal, title, color, existingChartVar) {
  const ctx = document.getElementById(canvasId);
  if (!ctx) return null;
  
  if (existingChartVar) {
    existingChartVar.destroy();
  }
  
  // Bound value
  const val = Math.min(Math.max(value, minVal), maxVal);
  const percent = ((val - minVal) / (maxVal - minVal)) * 100;
  
  const config = {
    type: 'doughnut',
    data: {
      labels: [title, 'Remaining'],
      datasets: [{
        data: [percent, 100 - percent],
        backgroundColor: [color, 'rgba(255,255,255,0.05)'],
        borderWidth: 0,
        hoverOffset: 0
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      circumference: 180,
      rotation: -90,
      cutout: '75%',
      plugins: {
        legend: { display: false },
        tooltip: { enabled: false }
      }
    },
    plugins: [{
      id: 'centerText',
      afterDraw(chart) {
        const { ctx, chartArea: { top, bottom, left, right, width, height } } = chart;
        ctx.save();
        ctx.font = 'bold 0.95rem Outfit, sans-serif';
        ctx.fillStyle = '#ffffff';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        const displayVal = value > 100 ? '> 100.0' : value.toFixed(2);
        ctx.fillText(displayVal, left + width / 2, top + height * 0.85);
        ctx.restore();
      }
    }]
  };
  
  return new Chart(ctx, config);
}

function showSignalDetail(signal) {
  if (!signal) return;
  const panel = document.getElementById('signal-detail-panel');
  const title = document.getElementById('signal-detail-title');
  const body = document.getElementById('signal-detail-body');
  if (!panel || !title || !body) return;

  title.textContent = `${signal.drug} — ${signal.event}`;
  
  // Calculate flags for consensus display
  let consensusFlags = [];
  if (signal.prr > 2.0 && signal.reports >= 3) consensusFlags.push("PRR Alert (>2.0)");
  if (signal.ror > 2.0 && signal.reports >= 3) consensusFlags.push("ROR Alert (>2.0)");
  if (signal.bcpnn_ic > 1.5) consensusFlags.push("BCPNN IC Alert (>1.5)");
  
  const consensusText = consensusFlags.length >= 2 
    ? `<span style="color:#10b981; font-weight:bold;">✅ CONSENSUS SIGNAL MET</span> (${consensusFlags.length}/3 Methods Flagged)`
    : `<span style="color:#f59e0b; font-weight:bold;">⚠️ WEAK/NO CONSENSUS</span> (${consensusFlags.length}/3 Methods Flagged)`;

  body.innerHTML = `
    <div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap:1rem; margin-bottom:1rem;">
      <div class="stat-mini"><div class="stat-mini__val prr-${signal.sev}">${signal.prr !== undefined ? signal.prr.toFixed(2) : 'N/A'}</div><div class="stat-mini__lbl">PRR Score</div></div>
      <div class="stat-mini"><div class="stat-mini__val" style="color: #8b5cf6;">${signal.ror !== undefined ? signal.ror.toFixed(2) : 'N/A'}</div><div class="stat-mini__lbl">ROR Score</div></div>
      <div class="stat-mini"><div class="stat-mini__val" style="color: #10b981;">${signal.bcpnn_ic !== undefined ? signal.bcpnn_ic.toFixed(2) : 'N/A'}</div><div class="stat-mini__lbl">BCPNN IC Score</div></div>
      <div class="stat-mini"><div class="stat-mini__val">${signal.reports.toLocaleString()}</div><div class="stat-mini__lbl">Reports (n)</div></div>
    </div>
    
    <div style="background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.05); padding: 0.8rem; border-radius: 6px; margin-bottom: 1rem; font-size: 0.9rem;">
      <strong>Consensus Analysis:</strong> ${consensusText}<br/>
      <span style="font-size: 0.8rem; color: var(--text-muted);">Flagged by: ${consensusFlags.join(', ') || 'None'}</span>
    </div>
    
    <div style="background: rgba(15, 23, 42, 0.4); border-radius: 6px; padding: 1rem; border: 1px solid rgba(255,255,255,0.03);">
      <h4 style="margin-top:0; margin-bottom:0.5rem; font-size:0.9rem; font-weight:600;">Statistical Summary (2x2 FAERS Contingency):</h4>
      <table style="width:100%; border-collapse:collapse; font-size:0.85rem; text-align:left;">
        <thead>
          <tr style="border-bottom:1px solid rgba(255,255,255,0.1); color:var(--text-muted);">
            <th style="padding:4px;">Metric Description</th>
            <th style="padding:4px; text-align:right;">Report Count</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td style="padding:4px;">Reports with drug <strong>${signal.drug}</strong> and event <strong>${signal.event}</strong> (a)</td>
            <td style="padding:4px; text-align:right; font-weight:bold;">${(signal.a || 0).toLocaleString()}</td>
          </tr>
          <tr>
            <td style="padding:4px;">Reports with drug <strong>${signal.drug}</strong> without event (b)</td>
            <td style="padding:4px; text-align:right; font-weight:bold;">${(signal.b || 0).toLocaleString()}</td>
          </tr>
          <tr>
            <td style="padding:4px;">Reports for other drugs with event <strong>${signal.event}</strong> (c)</td>
            <td style="padding:4px; text-align:right; font-weight:bold;">${(signal.c || 0).toLocaleString()}</td>
          </tr>
          <tr>
            <td style="padding:4px;">Reports for other drugs without event (d)</td>
            <td style="padding:4px; text-align:right; font-weight:bold;">${(signal.d || 0).toLocaleString()}</td>
          </tr>
        </tbody>
      </table>
      ${(signal.c === 0 || signal.prr > 500) ? `
        <div style="margin-top:0.75rem; padding:0.5rem 0.75rem; background:rgba(59,130,246,0.12); border-left:3px solid #3b82f6; border-radius:4px; font-size:0.8rem; color:#93c5fd; line-height:1.4;">
          <strong>* Exclusive Local Cohort Signal:</strong> Zero background reports were recorded for other drugs in this local cohort (c = 0). Haldane-Anscombe continuity correction (+0.5) was applied to prevent division-by-zero, resulting in asymptotic disproportionality values.
        </div>` : ''}
    </div>
  `;

  // Draw the half doughnut gauges
  setTimeout(() => {
    gaugePRRChart = renderGauge('gauge-prr-canvas', signal.prr || 0, 0, 10, 'PRR', '#3b82f6', gaugePRRChart);
    gaugeRORChart = renderGauge('gauge-ror-canvas', signal.ror || 0, 0, 10, 'ROR', '#8b5cf6', gaugeRORChart);
    gaugeICChart = renderGauge('gauge-ic-canvas', signal.bcpnn_ic || 0, -2, 5, 'BCPNN IC', '#10b981', gaugeICChart);
  }, 50);

  panel.style.display = 'block';
  panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });

  // Render D3 graph
  renderSignalNetworkGraph(signal.drug, signal.event);
}

function filterSignals() {
  const query = (document.getElementById('signal-search')?.value || '').toLowerCase();
  const sev = document.getElementById('signal-severity')?.value || 'all';
  const src = document.getElementById('signal-source')?.value || 'all';
  
  const showNone = document.getElementById('signal-show-none')?.checked === true;

  let filtered = signalsData;
  if (query) filtered = filtered.filter(s => s.drug.toLowerCase().includes(query) || s.event.toLowerCase().includes(query));
  if (sev !== 'all') {
    filtered = filtered.filter(s => s.sev === sev);
  } else if (!showNone) {
    // pairs that do not meet the signal criteria are hidden unless asked for
    filtered = filtered.filter(s => s.sev !== 'none');
  }
  if (src !== 'all') {
    filtered = filtered.filter(s => {
      let mappedSource = s.source;
      if (s.source === 'faers' || s.source === 'openfda') {
        mappedSource = 'fda';
      } else if (s.source === 'ner_clinical') {
        mappedSource = 'ehr';
      }
      return mappedSource === src;
    });
  }
  renderSignalsTable(filtered);
}

async function renderSignalNetworkGraph(drug, event) {
  const container = document.getElementById('signal-network-wrap');
  const card = document.getElementById('signal-network-card');
  if (!container || !card) return;
  
  card.style.display = 'block';
  container.innerHTML = `<div style="display:flex; justify-content:center; align-items:center; height:100%; color:var(--text-muted); font-size:0.9rem;">
    <span style="animation: spin 1s linear infinite; display:inline-block; margin-right: 8px;">⏳</span> Loading network neighborhood...
  </div>`;
  
  try {
    const res = await fetch(`/api/signals/network?drug=${encodeURIComponent(drug)}&event=${encodeURIComponent(event)}`);
    if (!res.ok) throw new Error("Network response not ok");
    const data = await res.json();
    
    container.innerHTML = '';
    
    const width = container.clientWidth || 600;
    const height = container.clientHeight || 380;
    
    const nodeMap = new Set((data.nodes || []).map(n => n.id));
    const safeLinks = (data.links || []).filter(l => {
      const src = typeof l.source === 'object' ? l.source.id : l.source;
      const tgt = typeof l.target === 'object' ? l.target.id : l.target;
      return nodeMap.has(src) && nodeMap.has(tgt);
    });

    const svg = d3.select(container)
      .append('svg')
      .attr('width', '100%')
      .attr('height', '100%')
      .attr('viewBox', `0 0 ${width} ${height}`)
      .attr('style', 'max-width: 100%; height: auto;');
      
    const simulation = d3.forceSimulation(data.nodes)
      .force('link', d3.forceLink(safeLinks).id(d => d.id).distance(90))
      .force('charge', d3.forceManyBody().strength(-150))
      .force('center', d3.forceCenter(width / 2, height / 2))
      .force('collision', d3.forceCollide().radius(d => d.val + 8));
      
    const link = svg.append('g')
      .selectAll('line')
      .data(safeLinks)
      .enter()
      .append('line')
      .attr('stroke', d => {
        if (d.type === 'primary') return '#ef4444';
        if (d.type === 'cross_link') return 'rgba(239, 68, 68, 0.4)';
        return 'rgba(255, 255, 255, 0.15)';
      })
      .attr('stroke-width', d => {
        if (d.type === 'primary') return 3.5;
        return 1.5;
      })
      .attr('stroke-dasharray', d => d.type === 'cross_link' ? '4,4' : 'none');

    const colorScale = type => {
      switch (type) {
        case 'drug': return '#3b82f6';
        case 'event': return '#ef4444';
        case 'co_drug': return '#f59e0b';
        case 'co_event': return '#10b981';
        default: return '#94a3b8';
      }
    };
    
    const node = svg.append('g')
      .selectAll('.node-group')
      .data(data.nodes)
      .enter()
      .append('g')
      .attr('class', 'node-group')
      .call(d3.drag()
        .on('start', dragstarted)
        .on('drag', dragged)
        .on('end', dragended)
      );
      
    node.append('circle')
      .attr('r', d => d.val)
      .attr('fill', d => colorScale(d.type))
      .attr('stroke', '#0f172a')
      .attr('stroke-width', 2)
      .attr('style', 'cursor: pointer; transition: filter 0.2s;')
      .on('mouseover', function() {
        d3.select(this).attr('filter', 'brightness(1.2)');
      })
      .on('mouseout', function() {
        d3.select(this).attr('filter', 'none');
      });
      
    node.append('text')
      .text(d => d.label)
      .attr('dx', 0)
      .attr('dy', d => d.val + 14)
      .attr('text-anchor', 'middle')
      .attr('fill', '#ffffff')
      .attr('font-size', '0.75rem')
      .attr('font-family', 'Outfit, sans-serif')
      .attr('style', 'pointer-events: none; text-shadow: 0 1px 3px rgba(0,0,0,0.8);');

    node.append('title')
      .text(d => `${d.label} (${d.type.toUpperCase()})`);
      
    simulation.on('tick', () => {
      link
        .attr('x1', d => (d.source && d.source.x != null) ? d.source.x : 0)
        .attr('y1', d => (d.source && d.source.y != null) ? d.source.y : 0)
        .attr('x2', d => (d.target && d.target.x != null) ? d.target.x : 0)
        .attr('y2', d => (d.target && d.target.y != null) ? d.target.y : 0);
        
      node
        .attr('transform', d => {
          const r = d.val;
          const x = Math.max(r, Math.min(width - r, d.x));
          const y = Math.max(r, Math.min(height - r, d.y));
          d.x = x;
          d.y = y;
          return `translate(${x},${y})`;
        });
    });
    
    function dragstarted(event, d) {
      if (!event.active) simulation.alphaTarget(0.3).restart();
      d.fx = d.x;
      d.fy = d.y;
    }
    
    function dragged(event, d) {
      d.fx = event.x;
      d.fy = event.y;
    }
    
    function dragended(event, d) {
      if (!event.active) simulation.alphaTarget(0);
      d.fx = null;
      d.fy = null;
    }
    
  } catch (err) {
    console.error("D3 network render error:", err);
    container.innerHTML = `<div style="display:flex; justify-content:center; align-items:center; height:100%; color:var(--text-muted); font-size:0.9rem;">
      ⚠️ Error loading network graph.
    </div>`;
  }
}


/* ──────────────── DRUG SEARCH ─────────────────────────────── */
let drugADEChart = null, drugTrendChart = null;

function initDrugSearch() {
  const input = document.getElementById('drug-search-input');
  const suggs = document.getElementById('drug-suggestions');
  const btn = document.getElementById('drug-search-btn');
  if (!input) return;

  let searchTimeout = null;

  input.addEventListener('input', () => {
    const q = input.value.trim();
    if (!q) { suggs.style.display = 'none'; return; }

    // Debounce search calls by 280ms for responsiveness
    clearTimeout(searchTimeout);
    suggs.innerHTML = `<li style="color:#888; padding:8px 12px;">Searching openFDA…</li>`;
    suggs.style.display = 'block';

    searchTimeout = setTimeout(async () => {
      const matches = await ApiLayer.searchDrugNames(q);
      if (matches.length === 0) {
        suggs.innerHTML = `<li style="color:#888; padding:8px 12px;">No results in openFDA for "${escHtml(q)}"</li>`;
      } else {
        suggs.innerHTML = matches.slice(0, 10).map(d =>
          `<li role="option" data-drug="${d}" style="padding:8px 12px; cursor:pointer;">${d}</li>`
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
    loadDrugProfile(li.dataset.drug);
  });

  btn.addEventListener('click', () => {
    suggs.style.display = 'none';
    loadDrugProfile(input.value.trim());
  });

  input.addEventListener('keydown', e => {
    if (e.key === 'Enter') { suggs.style.display = 'none'; loadDrugProfile(input.value.trim()); }
    if (e.key === 'Escape') suggs.style.display = 'none';
  });

  document.querySelectorAll('.drug-chip').forEach(chip => {
    chip.addEventListener('click', () => {
      input.value = chip.dataset.drug;
      loadDrugProfile(chip.dataset.drug);
    });
  });

  // Close suggestions when clicking outside
  document.addEventListener('click', e => {
    if (!e.target.closest('.drug-search-box')) suggs.style.display = 'none';
  });
}

async function loadDrugProfile(name) {
  const panel = document.getElementById('drug-profile-panel');
  const empty = document.getElementById('drug-search-empty');
  if (!panel || !empty) return;

  if (!name) {
    panel.style.display = 'none';
    empty.style.display = 'block';
    return;
  }

  // Show loading state implicitly by dimming panel
  if (panel.style.display === 'block') panel.style.opacity = '0.5';

  // ── Phase 1: Fast fetches (openFDA direct — ~1s) ──────────────────────────
  // These run first so the panel appears immediately without waiting for
  // the slow backend PRR/trials calls.
  const [labelData, eventsData] = await Promise.all([
    ApiLayer.fetchDrugLabel(name),
    ApiLayer.fetchDrugEvents(name, 5)
  ]);

  panel.style.opacity = '1';

  if (!eventsData || eventsData.ades.length === 0) {
    empty.style.display = 'block';
    panel.style.display = 'none';
    empty.innerHTML = `<h3>No statistical FAERS data found for "${name}".</h3><button class="btn btn--sm" onclick="location.reload()">Back</button>`;
    return;
  }

  empty.style.display = 'none';
  panel.style.display = 'block';
  panel.classList.add('fade-in');

  const totalReportsLocal = eventsData.totalReports;

  // Publish drug selection to the global DrugContext
  if (typeof DrugContext !== 'undefined') {
    DrugContext.set({
      drug: name,
      label: labelData,
      totalReports: totalReportsLocal,
      adeList: eventsData.ades,
      prr: null
    });
  }

  // Render header immediately with '…' placeholders for the slow badges
  document.getElementById('drug-profile-header').innerHTML = `
    <div style="font-size:3rem">💊</div>
    <div>
      <h2 style="font-size:1.8rem;font-weight:800;margin-bottom:0.25rem;text-transform:uppercase;">${escHtml(name)}</h2>
      <div style="opacity:0.85;margin-bottom:0.25rem;">Class: ${escHtml(labelData.class)}</div>
      <div style="font-size:0.85rem;opacity:0.7;margin-bottom:0.25rem;">Indication: ${escHtml(labelData.indication)}</div>
      <div style="font-size:0.8rem;opacity:0.65;margin-bottom:0.75rem;font-style:italic;">Dosage: ${escHtml(labelData.dosage)}</div>
      <div style="display:flex;gap:1rem;flex-wrap:wrap;margin-top:0.5rem;">
        <span id="dp-trials-badge" title="Active ClinicalTrials.gov studies for this drug"
              style="background:rgba(255,255,255,0.15);border-radius:6px;padding:4px 12px;font-size:0.8rem;font-weight:700;opacity:0.6;">
          🧪 Clinical Trials…
        </span>
        <span style="background:rgba(255,255,255,0.15);border-radius:6px;padding:4px 12px;font-size:0.8rem;font-weight:700;">
          ${totalReportsLocal.toLocaleString()} FAERS Reports (approx.)
        </span>
        <span id="dp-prr-badge" title="Proportional Reporting Ratio from live FAERS data"
              style="background:rgba(255,255,255,0.15);border-radius:6px;padding:4px 12px;font-size:0.8rem;font-weight:700;opacity:0.6;">
          PRR: …
        </span>
      </div>
    </div>
  `;

  // ── Phase 2: Slow fetches (backend — ~5-15s) — non-blocking ──────────────
  // Fire and forget. Update only the two badge elements when data arrives.
  // The rest of the panel is already visible to the user.
  const currentName = name; // capture for closure safety
  Promise.all([
    fetch(`/api/trials/${encodeURIComponent(currentName)}`)
      .then(r => r.json()).catch(() => null),
    fetch(`/api/prr-trials?drug=${encodeURIComponent(currentName)}&event=Nausea`)
      .then(r => r.json()).catch(() => null)
  ]).then(([trialsData, prrData]) => {
    const trialsBadge = document.getElementById('dp-trials-badge');
    const prrBadge    = document.getElementById('dp-prr-badge');

    if (trialsBadge && trialsData) {
      const n = trialsData.total_studies;
      const active = trialsData.active_safety_monitoring;
      trialsBadge.textContent = `🧪 ${n} Clinical Trial${n === 1 ? '' : 's'}${active ? ' (Active)' : ''}`;
      trialsBadge.style.opacity = '1';
    } else if (trialsBadge) {
      trialsBadge.textContent = '🧪 Trials: unavailable';
      trialsBadge.style.opacity = '0.5';
    }

    if (prrBadge && prrData?.prr != null) {
      const prr = prrData.prr.toFixed(2);
      const sig = prrData.is_signal;
      prrBadge.textContent = `PRR: ${prr}${sig ? ' ⚠ Signal' : ''}`;
      prrBadge.style.background = sig ? 'rgba(192,57,43,0.5)' : 'rgba(255,255,255,0.15)';
      prrBadge.style.opacity = '1';
      // Publish updated PRR back to context
      if (typeof DrugContext !== 'undefined') {
        DrugContext.set({ prr: prrData });
      }
    } else if (prrBadge) {
      prrBadge.textContent = 'PRR: unavailable';
      prrBadge.style.opacity = '0.5';
    }
  });

  // ADE bar chart
  const adeCtx = document.getElementById('drug-ade-chart');
  if (adeCtx) {
    if (drugADEChart) drugADEChart.destroy();
    drugADEChart = new Chart(adeCtx, {
      type: 'bar',
      data: {
        labels: eventsData.ades,
        datasets: [{
          label: 'FAERS Reports',
          data: eventsData.adeCounts,
          backgroundColor: ['#c0392b', '#e65100', '#f57c00', '#1565c0', '#2e7d32'],
          borderRadius: 6,
        }]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: { label: ctx => ` ${ctx.raw.toLocaleString()} reports` }
          }
        },
        scales: { x: { ticks: { maxRotation: 30 } } }
      }
    });
  }

  // Drug Trend chart — real LSTM data from /api/lstm (async update after initial render)
  const trendCtx = document.getElementById('drug-trend-chart');
  if (trendCtx) {
    if (drugTrendChart) drugTrendChart.destroy();
    // Empty until the monthly series arrives; nothing is drawn in its place
    drugTrendChart = new Chart(trendCtx, {
      type: 'line',
      data: {
        labels: [],
        datasets: [{
          label: 'Share of all reports (%)',
          data: [],
          borderColor: '#003d7c',
          backgroundColor: 'rgba(0,61,124,0.08)',
          tension: 0.4, fill: true, pointRadius: 4, pointHoverRadius: 6,
        }]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false } },
      }
    });
    // Monthly share of all reports from the loaded FAERS data
    fetch(`/api/lstm?drug=${encodeURIComponent(name)}`)
      .then(r => r.json())
      .then(data => {
        if (data.error || data.source === 'no_data' || !data.actual || !data.labels) return;
        drugTrendChart.data.labels = data.labels.slice(-24);
        drugTrendChart.data.datasets[0].data = data.actual.slice(-24);
        drugTrendChart.data.datasets[0].label = `Share of all reports (%), last ${Math.min(24, data.labels.length)} months`;
        drugTrendChart.update();
      })
      .catch(() => {}); // no data: the chart stays empty
  }

  // Signals list with illness-hover tooltips on event terms
  const sigList = document.getElementById('drug-signals-list');
  if (sigList) {
    sigList.innerHTML = eventsData.ades.slice(0, 3).map((event, i) => `
      <div class="alert-item alert-item--moderate" style="margin-bottom:0.5rem;">
        <div class="alert-item__icon">ℹ️</div>
        <div class="alert-item__body">
          <div class="alert-item__drug">
            <span class="illness-hover" data-illness="${escHtml(event)}" style="cursor:help;border-bottom:1px dashed #999;">${escHtml(event)}</span>
          </div>
          <div class="alert-item__meta">
            <span class="alert-item__prr">FAERS reports: ${eventsData.adeCounts[i].toLocaleString()}</span>
            <span class="alert-item__time">Source: openFDA/FAERS</span>
          </div>
        </div>
      </div>
    `).join('');
  }

  // Re-initialize tooltips to pick up new illness-hover elements
  if (typeof ApiLayer !== 'undefined') ApiLayer.initTooltips();
}

/* ──────────────── INTERACTION GRAPH (SECTION 1 & 2) ───────── */
/* NOTE: replaced by initInteractionsTab() in interactions_module.js */
function initInteractionGraph() {
  // Delegated to initInteractionsTab() — kept as no-op for legacy call sites
  if (typeof initInteractionsTab === 'function') initInteractionsTab();
}

/* ──────────────── CHARTS ──────────────────────────────────── */
let signalIntensityChart = null;
let prrDistChart = null;
let drugCatChart = null;
let throughputChart = null;
let kafkaLagChart = null;
let lstmDemoChart = null;

function buildLabels24h() {
  const now = new Date();
  return Array.from({ length: 24 }, (_, i) => {
    const h = new Date(now - (23 - i) * 3600000);
    return `${String(h.getHours()).padStart(2, '0')}:00`;
  });
}

function initSignalIntensityChart() {
  const ctx = document.getElementById('signal-intensity-chart');
  if (!ctx) return;
  if (signalIntensityChart) signalIntensityChart.destroy();
  // Initialize with zeroed arrays — async-filled from real SQLite signals cache
  const emptyLabels = Array.from({ length: 24 }, (_, i) => `${String(i).padStart(2,'0')}:00`);
  signalIntensityChart = new Chart(ctx, {
    type: 'line',
    data: {
      labels: emptyLabels,
      datasets: [
        {
          label: 'Strong signals',
          data: new Array(24).fill(0),
          borderColor: '#c0392b', backgroundColor: 'rgba(192,57,43,0.12)',
          tension: 0.4, fill: true, pointRadius: 2,
        },
        {
          label: 'Signals',
          data: new Array(24).fill(0),
          borderColor: '#e65100', backgroundColor: 'rgba(230,81,0,0.08)',
          tension: 0.4, fill: true, pointRadius: 2,
        },
        {
          label: 'No signal (computed)',
          data: new Array(24).fill(0),
          borderColor: '#0070c0', backgroundColor: 'rgba(0,112,192,0.08)',
          tension: 0.4, fill: true, pointRadius: 2,
        }
      ]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: { legend: { position: 'bottom', labels: { boxWidth: 12, font: { size: 11 } } } },
      scales: {
        x: { ticks: { maxTicksLimit: 12, font: { size: 10 } } },
        y: { beginAtZero: true, ticks: { font: { size: 10 } } }
      }
    }
  });
  // Async fill with real signal intensity data from SQLite
  fetch('/api/dashboard/signal-intensity')
    .then(r => r.json())
    .then(data => {
      if (!data.labels || !data.strong_signal) return;
      signalIntensityChart.data.labels = data.labels;
      signalIntensityChart.data.datasets[0].data = data.strong_signal;
      signalIntensityChart.data.datasets[1].data = data.signal;
      signalIntensityChart.data.datasets[2].data = data.none;
      signalIntensityChart.update();
    })
    .catch(() => {}); // silent fail — chart stays zeroed (not random)
}

function initDrugCategoryChart() {
  const ctx = document.getElementById('drug-category-chart');
  if (!ctx) return;
  if (drugCatChart) drugCatChart.destroy();
  const defaultLabels = ['Cardiovascular', 'Antibiotics', 'CNS/Psychiatric', 'Diabetes', 'Pain/NSAID', 'Other'];
  const defaultColors = ['#003d7c', '#0070c0', '#00695c', '#f0a500', '#c0392b', '#adb5bd'];
  // Initialize empty — async-filled from signals cache distribution
  drugCatChart = new Chart(ctx, {
    type: 'doughnut',
    data: {
      labels: defaultLabels,
      datasets: [{ data: [0, 0, 0, 0, 0, 0], backgroundColor: defaultColors, borderWidth: 2, borderColor: '#fff' }]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { position: 'bottom', labels: { boxWidth: 10, font: { size: 10 } } } }
    }
  });
  // Async fill with real drug category distribution
  fetch('/api/dashboard/drug-categories')
    .then(r => r.json())
    .then(data => {
      if (!data.labels || !data.data) return;
      drugCatChart.data.labels = data.labels;
      drugCatChart.data.datasets[0].data = data.data;
      drugCatChart.update();
    })
    .catch(() => {}); // silent fail
}

function initPRRDistributionChart() {
  const ctx = document.getElementById('prr-distribution-chart');
  if (!ctx) return;
  if (prrDistChart) prrDistChart.destroy();
  const bins = ['1.0–1.5', '1.5–2.0', '2.0–2.5', '2.5–3.0', '3.0–3.5', '3.5–4.0', '4.0–5.0', '5.0+'];
  prrDistChart = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: bins,
      datasets: [{
        label: 'Drug-Event Pairs',
        data: [0, 0, 0, 0, 0, 0, 0, 0],
        backgroundColor: bins.map((_, i) => i < 2 ? '#90caf9' : i < 4 ? '#f0a500' : '#c0392b'),
        borderRadius: 6,
      }]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { label: ctx => ` ${ctx.raw.toLocaleString()} pairs` } }
      },
      scales: {
        x: { title: { display: true, text: 'PRR Range', font: { size: 11 } } },
        y: { title: { display: true, text: 'Number of Pairs', font: { size: 11 } }, beginAtZero: true }
      }
    }
  });
  // Async fill with real PRR distribution data
  fetch('/api/dashboard/prr-distribution')
    .then(r => r.json())
    .then(data => {
      if (!data.labels || !data.data) return;
      prrDistChart.data.labels = data.labels;
      prrDistChart.data.datasets[0].data = data.data;
      prrDistChart.update();
    })
}



/* ──────────────── ANIMATED COUNTERS ───────────────────────── */
function animateCounter(el, target, duration = 1600, suffix = '') {
  const start = performance.now();
  const isFloat = String(target).includes('.');
  function step(now) {
    const progress = Math.min((now - start) / duration, 1);
    const ease = 1 - Math.pow(1 - progress, 3);
    const current = isFloat ? (target * ease).toFixed(1) : Math.floor(target * ease);
    el.textContent = Number(current).toLocaleString() + suffix;
    if (progress < 1) requestAnimationFrame(step);
  }
  requestAnimationFrame(step);
}

async function initCounters() {
  let stats = {
    reports_analyzed: 14283740,
    signals_detected: 47,
    drugs_monitored: 12490
  };
  try {
    const res = await fetch('/api/local-stats');
    const data = await res.json();
    if (data && !data.error) {
      stats.reports_analyzed = data.reports_analyzed || stats.reports_analyzed;
      stats.signals_detected = data.signals_detected || stats.signals_detected;
      stats.drugs_monitored = data.drugs_monitored || stats.drugs_monitored;
    }
  } catch (e) {
    console.warn("[Counters] Failed to fetch live local-stats, using fallback values:", e);
  }

  const elReports = document.querySelector('#stat-reports .stat-card__number');
  if (elReports) elReports.dataset.target = stats.reports_analyzed;

  const elSignals = document.querySelector('#stat-signals .stat-card__number');
  if (elSignals) elSignals.dataset.target = stats.signals_detected;

  const elDrugs = document.querySelector('#stat-drugs .stat-card__number');
  if (elDrugs) elDrugs.dataset.target = stats.drugs_monitored;

  document.querySelectorAll('[data-target]').forEach(el => {
    const target = parseFloat(el.dataset.target);
    animateCounter(el, target);
  });
}

/* ──────────────── SOURCE METERS ───────────────────────────── */
function animateMeters() {
  const meters = [
    { fill: document.getElementById('fda-bar'), val: document.getElementById('fda-val'), target: 72, label: '14,283 msg/min' },
    { fill: document.getElementById('ehr-bar'), val: document.getElementById('ehr-val'), target: 54, label: '9,840 msg/min' },
    { fill: document.getElementById('social-bar'), val: document.getElementById('social-val'), target: 38, label: '6,120 msg/min' },
    { fill: document.getElementById('ct-bar'), val: document.getElementById('ct-val'), target: 19, label: '2,340 msg/min' },
  ];
  setTimeout(() => {
    meters.forEach(m => {
      if (m.fill) m.fill.style.width = m.target + '%';
      if (m.val) m.val.textContent = m.label;
    });
  }, 400);
}

/* ──────────────── PIPELINE METRICS ────────────────────────── */
function updatePipelineMetrics() {
  // Kafka metrics are updated live by distributed_storage.js (startKafkaMetricBar).
  // Spark metrics are driven by distributed_storage.js (startSparkSimulation).
  // LSTM and NLP are fetched from real backend sources.
  // HDFS/HBase is an architecture illustration: no cluster is connected, so no size is shown.
  const hdfsEl = document.getElementById('hdfs-metric');
  if (hdfsEl) hdfsEl.textContent = 'Illustrative (not connected)';

  // LSTM metric — fetched from the real backend.
  // Shows which forecast is in use and its error on the held-out months.
  const lstmEl = document.getElementById('lstm-metric');
  if (lstmEl) {
    const updateLSTMMetric = async () => {
      try {
        const res = await fetch('/api/lstm?drug=Metformin');
        const data = await res.json();
        if (data.error) { lstmEl.textContent = 'Backend offline'; return; }
        if (data.source === 'no_data') {
          lstmEl.textContent = 'Not enough monthly data';
          lstmEl.style.color = '#e65100';
        } else if (data.model_used === 'lstm') {
          lstmEl.textContent = `LSTM · error ${data.model_error.toFixed(3)} pts (baseline ${data.baseline_error.toFixed(3)})`;
          lstmEl.style.color = '#2e7d32';
        } else {
          lstmEl.textContent = `Seasonal baseline · error ${data.baseline_error.toFixed(3)} pts`;
          lstmEl.style.color = '#555';
        }
      } catch (e) {
        lstmEl.textContent = 'Backend offline';
      }
    };
    updateLSTMMetric();
    setInterval(updateLSTMMetric, 30000);
  }

  // NLP metric — already handled by ml_models.js BioBERT throughput measurement.
  // No hardcoding needed here; ml_models.js pings the real BioBERT server.
  // We only set a fallback if that module isn't loaded.
  const nlpEl = document.getElementById('nlp-metric');
  if (nlpEl && typeof MLModels === 'undefined') {
    nlpEl.textContent = 'BioBERT module not loaded';
  }
}

/* ──────────────── RECENT ALERTS LIST ──────────────────────── */
async function renderRecentAlerts() {
  const list = document.getElementById('recent-alerts-list');
  if (!list) return;
  try {
    const res = await fetch('/api/signals');
    const data = await res.json();
    const signals = data.signals || [];
    
    // Sort by PRR descending
    signals.sort((x, y) => (y.prr || 0) - (x.prr || 0));

    // Update alert count badge in UI
    const badge = document.getElementById('alert-count-badge');
    if (badge) badge.textContent = signals.length;

    // Take top 5
    const actual = signals.filter(s => ['strong_signal', 'signal'].includes(normalizeSignalClass(s.signal_strength))
                                       && !(s.unresolved && s.unresolved.length));
    const topSignals = actual.slice(0, 5);
    
    if (topSignals.length === 0) {
      list.innerHTML = `<li class="alert-item" style="color:var(--text-muted); padding:1rem; text-align:center;">No recent safety alerts</li>`;
      return;
    }

    list.innerHTML = topSignals.map(s => {
      const meta = SIGNAL_CLASS_META[normalizeSignalClass(s.signal_strength)];
      return `
        <li class="alert-item alert-item--${meta.alert}">
          <div class="alert-item__icon">${meta.icon}</div>
          <div class="alert-item__body">
            <div class="alert-item__drug">${escHtml(s.drug)}</div>
            <div class="alert-item__event">${escHtml(s.event)}</div>
            <div class="alert-item__meta">
              <span class="alert-item__prr">PRR ${s.prr.toFixed(2)}</span>
              <span class="alert-item__time">${meta.label.toLowerCase()}</span>
            </div>
          </div>
        </li>
      `;
    }).join('');

    // Also update the top warning banner with the highest PRR signal!
    const alertText = document.getElementById('alert-text');
    if (alertText) {
      if (actual.length > 0) {
        const topSig = actual[0];
        alertText.innerHTML = `<strong>HIGHEST PRR IN THE LOADED DATA:</strong> <strong>${escHtml(topSig.drug)}</strong> — <strong>${escHtml(topSig.event)}</strong>, PRR ${topSig.prr.toFixed(2)} from ${(topSig.n_reports || topSig.a || 0).toLocaleString()} reports. A statistical signal, not a confirmed causal link.`;
      } else {
        alertText.innerHTML = `<strong>No monitored pair currently meets the signal criteria.</strong>`;
      }
    }
  } catch (e) {
    console.error("Failed to render recent alerts:", e);
    list.innerHTML = `<li class="alert-item" style="color:var(--text-muted); padding:1rem; text-align:center;">Failed to load alerts</li>`;
  }
}

/* ──────────────── CLUSTER GRID ────────────────────────────── */
function renderClusterGrid() {
  const grid = document.getElementById('cluster-grid');
  if (!grid) return;
  grid.innerHTML = CLUSTERS.map(c => `
    <div class="cluster-item">
      <div class="cluster-item__drugs">💊 ${c.drugs}</div>
      <div class="cluster-item__risk">⚠ ${c.risk} RISK</div>
      <div class="cluster-item__desc">${c.desc}</div>
    </div>
  `).join('');
}

/* ──────────────── PRR CALCULATOR ──────────────────────────── */
function initPRRCalculator() {
  const btn = document.getElementById('calc-prr-btn');
  if (!btn) return;
  btn.addEventListener('click', async () => {
    const result = document.getElementById('prr-result');
    if (!result) return;

    result.style.background = '#e8f0fe';
    result.innerHTML = '⏳ Fetching real data from openFDA…';

    try {
      // Use the drug from the Drug Search input if available, otherwise default
      const drugInput = document.getElementById('drug-search-input');
      const drug = (drugInput && drugInput.value.trim()) || 'Metformin';
      const event = 'Nausea'; // You can make this dynamic too

      const res = await fetch(`/api/prr-trials?drug=${encodeURIComponent(drug)}&event=${encodeURIComponent(event)}`);
      const data = await res.json();

      if (data.error) {
        result.style.background = '#fdecea';
        result.innerHTML = `Error: ${escHtml(data.error)}`;
        return;
      }

      // Fill the input boxes with the real values from openFDA
      const prrA = document.getElementById('prr-a');
      const prrB = document.getElementById('prr-b');
      const prrC = document.getElementById('prr-c');
      const prrD = document.getElementById('prr-d');
      if (prrA) prrA.value = data.a;
      if (prrB) prrB.value = data.b;
      if (prrC) prrC.value = data.c;
      if (prrD) prrD.value = data.d;

      const sigClass = normalizeSignalClass(data.signal_strength);
      const sig = SIGNAL_CLASS_META[sigClass].label;
      const col = sigClass === 'strong_signal' ? '#fdecea' : sigClass === 'signal' ? '#fff8e1' : '#e8f5e9';
      result.style.background = col;
      result.innerHTML = `PRR = <strong>${data.prr.toFixed(4)}</strong> &nbsp;|&nbsp; Signal Level: <strong>${sig}</strong><br><small style="opacity:0.7;">Drug: ${escHtml(data.drug)} | Event: ${escHtml(data.event)} | Source: openFDA FAERS (live)</small>`;

      // ── Registered-trials context panel ─────────────────────────────────────
      let ctPanel = document.getElementById('prr-ct-context');
      if (!ctPanel) {
        ctPanel = document.createElement('div');
        ctPanel.id = 'prr-ct-context';
        result.parentElement.appendChild(ctPanel);
      }

      const trialContext = {
        SIGNAL_AND_EVENT_UNDER_STUDY: { bg: '#fff8e1', border: '#f9a825', icon: '📋', label: 'SIGNAL · PAIR ALSO UNDER STUDY' },
        SIGNAL_AND_DRUG_UNDER_STUDY:  { bg: '#fff8e1', border: '#f9a825', icon: '📋', label: 'SIGNAL · DRUG UNDER STUDY' },
        SIGNAL_ONLY:                  { bg: '#fff3e0', border: '#e65100', icon: '⚠️', label: 'SIGNAL · NO REGISTERED TRIAL FOUND' },
        NO_SIGNAL:                    { bg: '#f5f5f5', border: '#9e9e9e', icon: 'ℹ️', label: 'NO SIGNAL' },
        NOT_AVAILABLE:                { bg: '#f5f5f5', border: '#9e9e9e', icon: 'ℹ️', label: 'NOT AVAILABLE' }
      };
      const cc = trialContext[data.trial_context] || trialContext.NOT_AVAILABLE;

      let trialsHtml = '';
      if (data.trial_matches && data.trial_matches.length > 0) {
        trialsHtml = `
          <div style="margin-top:8px; font-size:0.78rem;">
            <strong>Matching Active Trials:</strong>
            <ul style="margin:4px 0 0 1rem; padding:0;">
              ${data.trial_matches.map(t => `
                <li style="margin-bottom:3px;">
                  <a href="${escHtml(t.url)}" target="_blank" rel="noopener"
                     style="color:#003d7c; text-decoration:none;">
                    ${escHtml(t.nct_id)}
                  </a>
                  — ${escHtml(t.title.substring(0, 80))}${t.title.length > 80 ? '…' : ''}
                  <span style="color:#666;"> (${escHtml(t.phase)} · ${escHtml(t.status)})</span>
                </li>
              `).join('')}
            </ul>
          </div>`;
      }

      ctPanel.style.cssText = `margin-top:10px; padding:10px 14px; background:${cc.bg};
        border-left:4px solid ${cc.border}; border-radius:6px; font-size:0.82rem; line-height:1.5;`;
      ctPanel.innerHTML = `
        <div style="display:flex; align-items:center; gap:8px; margin-bottom:4px;">
          <span style="font-size:1.1rem;">${cc.icon}</span>
          <strong style="color:${cc.border};">ClinicalTrials.gov — ${cc.label}</strong>
          <span style="margin-left:auto; font-size:0.7rem; color:#999;">clinicaltrials.gov</span>
        </div>
        <div>${escHtml(data.trial_context_message)}</div>
        ${trialsHtml}
      `;
    } catch (e) {
      result.style.background = '#fdecea';
      result.innerHTML = `❌ Could not reach backend. Is <code>python backend/app.py</code> running?`;
    }
  });
}




/* ──────────────── NAVIGATION ──────────────────────────────── */
let chartsInitialized = {};

function navigateTo(sectionId) {
  document.querySelectorAll('.page-section').forEach(s => { s.style.display = 'none'; s.classList.remove('active-section'); });
  const target = document.getElementById(sectionId);
  if (target) { target.style.display = 'block'; target.classList.add('active-section'); }

  document.querySelectorAll('.main-nav__item').forEach(item => {
    item.classList.toggle('active', item.querySelector('[data-section]')?.dataset.section === sectionId);
  });

  window.scrollTo({ top: 0, behavior: 'smooth' });

  // Lazy-init charts per section
  if (sectionId === 'dashboard' && !chartsInitialized.dashboard) {
    chartsInitialized.dashboard = true;
    initSignalIntensityChart();
    initDrugCategoryChart();
    renderRecentAlerts();
    if (typeof DistributedStorage !== 'undefined') DistributedStorage.animateMeters();
  }
  if (sectionId === 'signals' && !chartsInitialized.signals) {
    chartsInitialized.signals = true;
    fetchAndRenderSignals();
  }
  if (sectionId === 'interactions' && !chartsInitialized.interactions) {
    chartsInitialized.interactions = true;
    if (typeof initInteractionsTab === 'function') initInteractionsTab();
  }
  if (sectionId === 'data-sources' && !chartsInitialized.datasources) {
    chartsInitialized.datasources = true;
    if (typeof DistributedStorage !== 'undefined') {
      DistributedStorage.initThroughputChart();
      DistributedStorage.initKafkaLagChart();
    }
  }
  if (sectionId === 'boxed-warnings' && !chartsInitialized.boxedwarnings) {
    chartsInitialized.boxedwarnings = true;
    // Section ready — user will trigger load via search/chips
  }
  if (sectionId === 'methodology' && !chartsInitialized.methodology) {
    chartsInitialized.methodology = true;
    if (typeof MLModels !== 'undefined') {
      MLModels.initLSTMDrugSelector();
      MLModels.initLSTMDemoChart('Metformin');
    }
  }
  if (sectionId === 'validation' && typeof loadValidation === 'function') {
    loadValidation();
  }
  if (sectionId === 'temporal-analysis') {
    if (!chartsInitialized.temporal) {
      chartsInitialized.temporal = true;
      if (typeof initTemporalTab === 'function') initTemporalTab();
    } else if (typeof runTemporalAnalysis === 'function') {
      setTimeout(() => runTemporalAnalysis(), 50);
    }
  }
  if (sectionId === 'demographics') {
    if (!chartsInitialized.demographics) {
      chartsInitialized.demographics = true;
      if (typeof initDemographicsTab === 'function') initDemographicsTab();
    } else if (typeof runDemographicsAnalysis === 'function') {
      setTimeout(() => runDemographicsAnalysis(), 50);
    }
  }

  // Auto-sync global DrugContext to active inputs on tab change
  if (typeof DrugContext !== 'undefined' && DrugContext.state.drug) {
    if (sectionId === 'boxed-warnings') {
      const bwInput = document.getElementById('bw-search-input');
      if (bwInput) {
        bwInput.value = DrugContext.state.drug;
        const resultsPanel = document.getElementById('bw-results-panel');
        if (resultsPanel && (resultsPanel.style.display !== 'block' || resultsPanel.dataset.drug !== DrugContext.state.drug)) {
          loadBoxedWarningAnalysis(DrugContext.state.drug);
        }
      }
    } else if (sectionId === 'interactions') {
      const inputA = document.getElementById('explorer-drug-a');
      if (inputA) {
        inputA.value = DrugContext.state.drug;
      }
    }
  }

  // Re-initialize tooltips if section changed
  setTimeout(() => {
    if (typeof ApiLayer !== 'undefined') ApiLayer.initTooltips();
  }, 300);
}

function initNavigation() {
  // Nav links
  document.querySelectorAll('.main-nav__link').forEach(link => {
    link.addEventListener('click', e => {
      e.preventDefault();
      navigateTo(link.dataset.section);
    });
  });

  // Footer links
  document.querySelectorAll('.footer-link[data-nav]').forEach(link => {
    link.addEventListener('click', e => {
      e.preventDefault();
      navigateTo(link.dataset.nav);
    });
  });

  // Hero buttons
  document.querySelectorAll('[data-nav]').forEach(el => {
    if (!el.classList.contains('footer-link')) {
      el.addEventListener('click', e => {
        e.preventDefault();
        navigateTo(el.dataset.nav);
      });
    }
  });

  // Mobile menu
  const menuBtn = document.getElementById('mobile-menu-btn');
  const navList = document.getElementById('main-nav-list');
  if (menuBtn && navList) {
    menuBtn.addEventListener('click', () => navList.classList.toggle('open'));
  }

  // Chart range buttons
  document.querySelectorAll('[data-chart-range]').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('[data-chart-range]').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      initSignalIntensityChart();
    });
  });
}

/* ──────────────── INTERACTION GRAPH EVENTS ─────────────────── */
// NOTE: initInteractionGraph is fully defined above (lines 484-586).
// The duplicate definition has been removed to prevent it from overwriting
// the working version that correctly references #graph-suggestions.


/* ──────────────── SIGNALS PAGE EVENTS ─────────────────────── */
function initSignalsPage() {
  document.getElementById('signal-search')?.addEventListener('input', filterSignals);
  document.getElementById('signal-severity')?.addEventListener('change', filterSignals);
  document.getElementById('signal-show-none')?.addEventListener('change', filterSignals);
  document.getElementById('signal-source')?.addEventListener('change', filterSignals);
  document.getElementById('refresh-signals-btn')?.addEventListener('click', async () => {
    const btn = document.getElementById('refresh-signals-btn');
    if (btn) {
      btn.disabled = true;
      btn.textContent = '⏳ Loading...';
    }
    await fetchAndRenderSignals(true);
    if (btn) {
      btn.disabled = false;
      btn.textContent = '↻ Refresh';
    }
  });
  document.getElementById('close-signal-detail')?.addEventListener('click', () => {
    const panel = document.getElementById('signal-detail-panel');
    if (panel) panel.style.display = 'none';
    const netCard = document.getElementById('signal-network-card');
    if (netCard) netCard.style.display = 'none';
  });
  
  initNerPanel();
}

let selectedNerFile = null;

function initNerPanel() {
  const dropZone = document.getElementById('ner-drop-zone');
  const fileInput = document.getElementById('ner-file-input');
  const fileInfo = document.getElementById('ner-file-info');
  const fileName = document.getElementById('ner-file-name');
  const removeBtn = document.getElementById('ner-remove-file-btn');
  const textInput = document.getElementById('ner-text-input');
  const mineBtn = document.getElementById('ner-mine-btn');
  const clearBtn = document.getElementById('ner-clear-btn');
  
  const progressWrap = document.getElementById('ner-progress-wrap');
  const progressText = document.getElementById('ner-progress-text');
  const progressPct = document.getElementById('ner-progress-pct');
  const progressBar = document.getElementById('ner-progress-bar');
  const progressLog = document.getElementById('ner-progress-log');
  const resultsContainer = document.getElementById('ner-results-container');
  const resultsGrid = document.getElementById('ner-results-grid');

  if (!dropZone || !mineBtn) return;

  // Open file selector on drop zone click
  dropZone.addEventListener('click', () => fileInput.click());

  // Prevent default drag behaviors
  ['dragenter', 'dragover', 'dragleave', 'drop'].forEach(eventName => {
    dropZone.addEventListener(eventName, e => {
      e.preventDefault();
      e.stopPropagation();
    }, false);
  });

  // Highlight drop zone
  ['dragenter', 'dragover'].forEach(eventName => {
    dropZone.addEventListener(eventName, () => {
      dropZone.style.borderColor = 'var(--primary)';
      dropZone.style.background = 'rgba(59, 130, 246, 0.08)';
    }, false);
  });

  ['dragleave', 'drop'].forEach(eventName => {
    dropZone.addEventListener(eventName, () => {
      dropZone.style.borderColor = 'rgba(59, 130, 246, 0.4)';
      dropZone.style.background = 'rgba(59, 130, 246, 0.02)';
    }, false);
  });

  // Handle dropped files
  dropZone.addEventListener('drop', e => {
    const dt = e.dataTransfer;
    const files = dt.files;
    if (files.length > 0) {
      handleNerFileSelect(files[0]);
    }
  });

  // Handle file input selection
  fileInput.addEventListener('change', () => {
    if (fileInput.files.length > 0) {
      handleNerFileSelect(fileInput.files[0]);
    }
  });

  function handleNerFileSelect(file) {
    selectedNerFile = file;
    fileName.textContent = file.name;
    fileInfo.style.display = 'flex';
    textInput.value = ''; // clear text input when file is chosen
  }

  // Remove selected file
  removeBtn.addEventListener('click', e => {
    e.stopPropagation();
    selectedNerFile = null;
    fileInput.value = '';
    fileInfo.style.display = 'none';
  });

  // Clear inputs and results
  clearBtn.addEventListener('click', () => {
    selectedNerFile = null;
    fileInput.value = '';
    fileInfo.style.display = 'none';
    textInput.value = '';
    progressWrap.style.display = 'none';
    resultsContainer.style.display = 'none';
    progressLog.innerHTML = '';
  });

  // Run NLP Mining task
  mineBtn.addEventListener('click', async () => {
    const textVal = textInput.value.trim();
    if (!selectedNerFile && !textVal) {
      alert("Please upload a file or enter clinical text to mine.");
      return;
    }

    progressWrap.style.display = 'block';
    progressLog.innerHTML = '<div>[System] Registering clinical mining task...</div>';
    updateProgress(10, 'Registering task...');
    resultsContainer.style.display = 'none';

    const formData = new FormData();
    if (selectedNerFile) {
      formData.append('file', selectedNerFile);
    } else {
      formData.append('text', textVal);
    }

    try {
      // Step 1: POST to create task
      const postRes = await fetch('/api/signals/ner-mine', {
        method: 'POST',
        body: formData
      });
      
      if (!postRes.ok) throw new Error("Could not start mining task.");
      const postData = await postRes.json();
      const taskId = postData.task_id;
      
      progressLog.innerHTML += `<div>[System] Task registered. Task ID: ${taskId}</div>`;
      updateProgress(20, 'Connecting stream...');

      // Step 2: Open SSE connection
      const sse = new EventSource(`/api/signals/ner-mine/stream/${taskId}`);
      
      sse.addEventListener('status', e => {
        const msg = e.data;
        progressLog.innerHTML += `<div>[Progress] ${escHtml(msg)}</div>`;
        progressLog.scrollTop = progressLog.scrollHeight;
        
        if (msg.includes('OCR: Rasterizing')) {
          updateProgress(30, msg);
        } else if (msg.includes('OCR: Transcribing')) {
          updateProgress(45, msg);
        } else if (msg.includes('NER:')) {
          updateProgress(65, msg);
        } else if (msg.includes('FAERS: Scoring')) {
          const match = msg.match(/pair (\d+)\/(\d+)/);
          if (match) {
            const current = parseInt(match[1]);
            const total = parseInt(match[2]);
            const pct = 70 + Math.floor((current / total) * 25);
            updateProgress(pct, msg);
          } else {
            updateProgress(80, msg);
          }
        } else {
          updateProgress(50, msg);
        }
      });

      sse.addEventListener('done', async e => {
        const resData = JSON.parse(e.data);
        sse.close();
        
        progressLog.innerHTML += `<div style="color:#10b981; font-weight:bold;">[Success] Mining completed!</div>`;
        updateProgress(100, 'Analysis complete.');
        
        // Show results grid
        renderNerResults(resData.signals);
        
        // Refresh signals list
        await fetchAndRenderSignals();
      });

      sse.addEventListener('error', e => {
        const errMsg = e.data || "Unknown streaming error occurred.";
        progressLog.innerHTML += `<div style="color:#ef4444; font-weight:bold;">[Error] ${escHtml(errMsg)}</div>`;
        sse.close();
        updateProgress(0, 'Task failed.');
      });

    } catch (err) {
      progressLog.innerHTML += `<div style="color:#ef4444; font-weight:bold;">[Error] ${escHtml(err.message)}</div>`;
      updateProgress(0, 'Task failed.');
    }
  });

  function updateProgress(pct, text) {
    progressBar.style.width = `${pct}%`;
    progressPct.textContent = `${pct}%`;
    progressText.textContent = text;
  }

  function renderNerResults(signals) {
    resultsContainer.style.display = 'block';
    
    if (signals.length === 0) {
      resultsGrid.innerHTML = `
        <div style="grid-column: 1/-1; padding: 1.5rem; text-align: center; color: var(--text-muted); background: rgba(0,0,0,0.1); border-radius: 6px;">
          No statistically significant drug-event signals detected in the clinical narrative.
        </div>`;
      return;
    }

    resultsGrid.innerHTML = signals.map(s => {
      return `
        <div class="dashboard-card" style="margin: 0; padding: 1rem; border: 1px solid rgba(255, 255, 255, 0.05); background: rgba(15, 23, 42, 0.3);">
          <div style="display: flex; justify-content: space-between; align-items: start; margin-bottom: 0.5rem;">
            <span class="status-pill status-pill--${SIGNAL_CLASS_META[normalizeSignalClass(s.signal_strength)].pill}">${SIGNAL_CLASS_META[normalizeSignalClass(s.signal_strength)].label}</span>
            <span style="font-size: 0.75rem; color: var(--text-muted);">Source: EHR NER</span>
          </div>
          <h4 style="font-size: 1rem; font-weight: 700; margin: 0.2rem 0;">${escHtml(s.drug)}</h4>
          <p style="font-size: 0.85rem; color: var(--text-muted); margin: 0 0 0.8rem 0;">↳ ${escHtml(s.event)}</p>
          <div style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 0.5rem; text-align: center; font-size: 0.8rem; background: rgba(0,0,0,0.2); padding: 0.5rem; border-radius: 4px;">
            <div>
              <div style="font-weight: bold; color: #3b82f6;">${s.prr.toFixed(2)}</div>
              <div style="font-size: 0.65rem; color: var(--text-muted);">PRR</div>
            </div>
            <div>
              <div style="font-weight: bold; color: #8b5cf6;">${s.ror.toFixed(2)}</div>
              <div style="font-size: 0.65rem; color: var(--text-muted);">ROR</div>
            </div>
            <div>
              <div style="font-weight: bold; color: #10b981;">${s.bcpnn_ic.toFixed(2)}</div>
              <div style="font-size: 0.65rem; color: var(--text-muted);">IC</div>
            </div>
          </div>
          <div style="margin-top: 0.8rem; font-size: 0.75rem; display: flex; justify-content: space-between; color: var(--text-muted);">
            <span>Co-reports: <strong>${s.n_reports}</strong></span>
            <span style="color: #10b981; font-weight: 500;">Signal Persistent</span>
          </div>
        </div>
      `;
    }).join('');
  }
}

/* ──────────────── BOXED WARNING ANALYSIS ──────────────────── */
let bwEventsChart = null;

function initBoxedWarnings() {
  const input = document.getElementById('bw-search-input');
  const suggs = document.getElementById('bw-suggestions');
  const btn = document.getElementById('bw-search-btn');
  if (!input) return;

  let searchTimeout = null;

  // Autocomplete
  input.addEventListener('input', () => {
    const q = input.value.trim();
    if (!q || q.length < 2) { suggs.style.display = 'none'; return; }

    clearTimeout(searchTimeout);
    suggs.innerHTML = `<li style="color:#888; padding:8px 12px;">Searching openFDA…</li>`;
    suggs.style.display = 'block';

    searchTimeout = setTimeout(async () => {
      if (typeof ApiLayer === 'undefined') return;
      const matches = await ApiLayer.searchDrugNames(q);
      if (matches.length === 0) {
        suggs.innerHTML = `<li style="color:#888; padding:8px 12px;">No results for "${escHtml(q)}"</li>`;
      } else {
        suggs.innerHTML = matches.slice(0, 8).map(d =>
          `<li role="option" data-drug="${d}" style="padding:8px 12px; cursor:pointer;">${d}</li>`
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
    loadBoxedWarningAnalysis(li.dataset.drug);
  });

  btn?.addEventListener('click', () => {
    suggs.style.display = 'none';
    const drug = input.value.trim();
    if (drug) loadBoxedWarningAnalysis(drug);
  });

  input.addEventListener('keydown', e => {
    if (e.key === 'Enter') { suggs.style.display = 'none'; const drug = input.value.trim(); if (drug) loadBoxedWarningAnalysis(drug); }
    if (e.key === 'Escape') suggs.style.display = 'none';
  });

  // Preset drug chips
  document.querySelectorAll('.bw-chip').forEach(chip => {
    chip.addEventListener('click', () => {
      input.value = chip.dataset.drug;
      loadBoxedWarningAnalysis(chip.dataset.drug);
    });
  });

  // Close suggestions on outside click
  document.addEventListener('click', e => {
    if (!e.target.closest('#bw-search-input') && !e.target.closest('#bw-suggestions')) {
      suggs.style.display = 'none';
    }
  });
}

async function loadBoxedWarningAnalysis(drugName) {
  const resultsPanel = document.getElementById('bw-results-panel');
  if (resultsPanel) {
    resultsPanel.dataset.drug = drugName;
  }
  const loadingDiv = document.getElementById('bw-loading');
  const noWarningDiv = document.getElementById('bw-no-warning');
  const hasWarningDiv = document.getElementById('bw-has-warning');
  const emptyState = document.getElementById('bw-empty-state');

  emptyState.style.display = 'none';
  if (resultsPanel) resultsPanel.style.display = 'block';
  loadingDiv.style.display = 'block';
  noWarningDiv.style.display = 'none';
  hasWarningDiv.style.display = 'none';

  // Hide literature panels until data arrives
  ['bw-literature-card','bw-timeline-card','bw-violations-card','bw-bias-card']
    .forEach(id => { const el = document.getElementById(id); if (el) el.style.display = 'none'; });

  try {
    const [warningRes, eventsRes, trialsRes] = await Promise.all([
      fetch(`/api/boxed-warning/${encodeURIComponent(drugName)}`).then(r => r.json()),
      fetch(`/api/boxed-warning-events/${encodeURIComponent(drugName)}`).then(r => r.json()),
      fetch(`/api/trials/${encodeURIComponent(drugName)}`).then(r => r.json()).catch(() => null)
    ]);

    loadingDiv.style.display = 'none';

    if (!warningRes.has_warning) {
      noWarningDiv.style.display = 'block';
      document.getElementById('bw-no-warning-title').textContent = `No Boxed Warning Found for "${drugName}"`;
      if (trialsRes && trialsRes.total_studies > 0) {
        let noWarnExtra = document.getElementById('bw-no-warning-ct');
        if (!noWarnExtra) {
          noWarnExtra = document.createElement('div');
          noWarnExtra.id = 'bw-no-warning-ct';
          noWarnExtra.style.cssText = 'margin-top:1rem; padding:10px 14px; background:#e8f5e9; border-left:4px solid #2e7d32; border-radius:6px; font-size:0.83rem; text-align:left;';
          noWarningDiv.appendChild(noWarnExtra);
        }
        noWarnExtra.innerHTML = `🧪 <strong>${trialsRes.total_studies} ClinicalTrials</strong> found for "${escHtml(drugName)}" (${trialsRes.recruiting_count} recruiting). No boxed warning, but drug is being studied.`;
      }
      return;
    }

    hasWarningDiv.style.display = 'block';

    document.getElementById('bw-drug-header').innerHTML = `
      <div style="display:flex; align-items:center; gap:1.5rem;">
        <div style="font-size:3rem;">⚠️</div>
        <div>
          <h2 style="font-size:1.6rem; font-weight:800; margin-bottom:0.25rem; text-transform:uppercase;">${escHtml(drugName)}</h2>
          <div style="opacity:0.85; font-size:0.85rem;">${escHtml(warningRes.generic_name || '')} ${warningRes.brand_name ? '(' + escHtml(warningRes.brand_name) + ')' : ''}</div>
          <div style="opacity:0.65; font-size:0.8rem; margin-top:0.25rem;">${escHtml(warningRes.pharm_class || '')} · ${escHtml(warningRes.route || '')}</div>
          <div style="margin-top:0.5rem;">
            <span style="background:rgba(192,57,43,0.8); padding:3px 10px; border-radius:4px; font-size:0.75rem; font-weight:700;">⬛ HAS BOXED WARNING</span>
          </div>
        </div>
      </div>`;

    document.getElementById('bw-warning-text').innerHTML = escHtml(warningRes.warning_text)
      .replace(/WARNING/g, '<strong>WARNING</strong>')
      .replace(/BOXED WARNING/g, '<strong style="color:#c0392b;">BOXED WARNING</strong>');

    document.getElementById('bw-total-reports').textContent = eventsRes.total_reports.toLocaleString();
    document.getElementById('bw-warned-count').textContent = eventsRes.boxed_warning_event_count.toLocaleString();
    document.getElementById('bw-warned-pct').textContent = eventsRes.boxed_warning_percentage + '%';
    document.getElementById('bw-other-count').textContent = eventsRes.other_events.length;

    // ClinicalTrials monitoring card
    let ctMonitorCard = document.getElementById('bw-ct-monitor-card');
    if (!ctMonitorCard) {
      ctMonitorCard = document.createElement('div');
      ctMonitorCard.id = 'bw-ct-monitor-card';
      ctMonitorCard.style.cssText = 'margin-bottom:1.5rem;';
      const chartGrid = document.querySelector('#bw-has-warning .dashboard-grid');
      if (chartGrid) chartGrid.parentElement.insertBefore(ctMonitorCard, chartGrid);
    }
    if (trialsRes) {
      const n = trialsRes.total_studies;
      const rec = trialsRes.recruiting_count;
      const phase34 = trialsRes.studies?.filter(s => s.phase && (s.phase.includes('PHASE3') || s.phase.includes('PHASE4'))).length ?? 0;
      let cardBg, cardBorder, cardIcon, cardHeading, cardSubtext;
      if (n === 0) {
        cardBg='#fef2f2'; cardBorder='#c0392b'; cardIcon='🚨';
        cardHeading='No Active Registered Trials Found';
        cardSubtext=`<strong>0 active ClinicalTrials.gov studies</strong> list "${escHtml(drugName)}" as an intervention.`;
      } else if (n <= 3) {
        cardBg='#fff8e1'; cardBorder='#f9a825'; cardIcon='⚠️';
        cardHeading=`${n} Active Registered Stud${n===1?'y':'ies'}`;
        cardSubtext=`<strong>${n} active trial${n===1?'':'s'}</strong> (${rec} recruiting) found.`;
      } else {
        cardBg='#e8f5e9'; cardBorder='#2e7d32'; cardIcon='✅';
        cardHeading=`${n} Active Registered Trial${n===1?'':'s'}`;
        cardSubtext=`<strong>${n} active ClinicalTrials</strong> (${rec} recruiting, ${phase34} Phase 3/4).`;
      }
      const topStudies = (trialsRes.studies || []).slice(0, 5);
      const studyLinks = topStudies.map(s => `
        <li><a href="${escHtml(s.url)}" target="_blank" rel="noopener"
           style="color:#003d7c; font-weight:600; text-decoration:none;">${escHtml(s.nct_id)}</a>
         — ${escHtml(s.title.substring(0,90))}${s.title.length>90?'…':''}
         <span style="color:#666;"> · ${escHtml(s.phase)} · ${escHtml(s.status)}</span>
        </li>`).join('');
      ctMonitorCard.innerHTML = `
        <div style="padding:14px 16px; background:${cardBg}; border-left:4px solid ${cardBorder}; border-radius:8px; font-size:0.85rem; line-height:1.6;">
          <div style="display:flex; align-items:center; gap:8px; margin-bottom:6px;">
            <span style="font-size:1.2rem;">${cardIcon}</span>
            <strong style="color:${cardBorder}; font-size:0.9rem;">ClinicalTrials.gov Monitoring Status</strong>
            <span style="margin-left:auto; font-size:0.7rem; color:#999;">clinicaltrials.gov · live</span>
          </div>
          <div style="font-weight:700; margin-bottom:4px;">${cardHeading}</div>
          <div>${cardSubtext}</div>
          ${topStudies.length ? `<div style="margin-top:10px; font-size:0.78rem;"><strong>Recent Studies:</strong><ul style="margin:4px 0 0 1rem; padding:0; line-height:1.8;">${studyLinks}</ul></div>` : ''}
        </div>`;
    }

    // Events bar chart
    const chartCtx = document.getElementById('bw-events-chart');
    if (chartCtx) {
      if (bwEventsChart) bwEventsChart.destroy();
      const allEvents = [...eventsRes.warned_events, ...eventsRes.other_events]
        .sort((a,b) => b.count - a.count).slice(0,15);
      bwEventsChart = new Chart(chartCtx, {
        type: 'bar',
        data: {
          labels: allEvents.map(e => e.term.length > 25 ? e.term.substring(0,22)+'…' : e.term),
          datasets: [{ label: 'FAERS Reports', data: allEvents.map(e => e.count),
            backgroundColor: allEvents.map(e => e.is_boxed_warning ? '#c0392b' : '#3498db'), borderRadius: 4 }]
        },
        options: {
          indexAxis: 'y', responsive: true, maintainAspectRatio: false,
          plugins: { legend: { display: false },
            tooltip: { callbacks: { label: ctx => {
              const ev = allEvents[ctx.dataIndex];
              return ` ${ctx.raw.toLocaleString()} reports (${ev.percentage}%) ${ev.is_boxed_warning?'⚠ BOXED WARNING':''}`;
            }}}},
          scales: { x: { title: { display: true, text: 'FAERS Report Count' } }, y: { ticks: { font: { size: 10 } } } }
        }
      });
    }

    // Events table
    const tableDiv = document.getElementById('bw-events-table');
    if (tableDiv) {
      const allSorted = [...eventsRes.warned_events, ...eventsRes.other_events].sort((a,b) => b.count - a.count);
      tableDiv.innerHTML = `
        <table style="width:100%; font-size:0.8rem; border-collapse:collapse;">
          <thead>
            <tr style="border-bottom:2px solid #ddd; text-align:left; background:#f8f9fa;">
              <th style="padding:6px 8px;">Adverse Event</th>
              <th style="padding:6px 8px;">Reports (n)</th>
              <th style="padding:6px 8px;">%</th>
              <th style="padding:6px 8px;">Boxed Warning?</th>
            </tr>
          </thead>
          <tbody>
            ${allSorted.map(ev => `
              <tr style="border-bottom:1px solid #eee; ${ev.is_boxed_warning ? 'background:#fef2f2;' : ''}">
                <td style="padding:6px 8px; font-weight:${ev.is_boxed_warning ? '700' : '400'}; color:${ev.is_boxed_warning ? '#c0392b' : 'inherit'}">${escHtml(ev.term)}</td>
                <td style="padding:6px 8px;">${ev.count.toLocaleString()}</td>
                <td style="padding:6px 8px;">${ev.percentage}%</td>
                <td style="padding:6px 8px;">${ev.is_boxed_warning ? '<span style="color:#c0392b; font-weight:700;">⚠ YES</span>' : '<span style="color:#999;">No</span>'}</td>
              </tr>
            `).join('')}
          </tbody>
        </table>
        <div style="margin-top:0.75rem; padding:8px 12px; background:#f8f9fa; border-radius:6px; font-size:0.75rem; color:#666;">
          <strong style="color:#c0392b;">■</strong> Red = Events matching the FDA Boxed Warning &nbsp;
          <strong style="color:#3498db;">■</strong> Blue = Other reported adverse events
        </div>
      `;
    }

    // ── Phase 2: Show skeletons immediately, then fire API calls in parallel ──
    // Skeletons give instant visual feedback while the backend fetches data.
    showSecondarySkeletons();

    const drug = drugName;
    Promise.all([
      fetch(`/api/boxed-warning/timeline/${encodeURIComponent(drug)}`).then(r => r.json()).catch(() => null),
      fetch(`/api/boxed-warning/post-warning-reports/${encodeURIComponent(drug)}`).then(r => r.json()).catch(() => null),
      fetch(`/api/boxed-warning/bias-analysis/${encodeURIComponent(drug)}`).then(r => r.json()).catch(() => null)
    ]).then(([timelineData, violationsData, biasData]) => {
      renderTimelineChart(timelineData);
      renderViolationsLog(drug, violationsData);
      renderBiasChart(biasData);
      renderLiteratureCard(drug, eventsRes, violationsData, timelineData, biasData);
    }).catch(e => {
      console.warn('[PharmaWatch] Secondary boxed-warning analyses failed:', e.message);
      // Replace all skeletons with a generic error notice
      ['bw-literature-card','bw-timeline-card','bw-violations-card','bw-bias-card'].forEach(id => {
        const el = document.getElementById(id);
        if (el && el.dataset.skeleton === 'true') {
          el.innerHTML = `<div style="text-align:center; padding:1.5rem; color:#888; font-size:0.85rem;">⚠️ Could not load — backend may be offline.</div>`;
          el.dataset.skeleton = '';
        }
      });
    });

  } catch (err) {
    loadingDiv.style.display = 'none';
    noWarningDiv.style.display = 'block';
    document.getElementById('bw-no-warning-title').textContent = `❌ Error: Is the Python backend running? (${err.message})`;
  }
}

/* ──────────────── SKELETON SCREENS (Boxed Warning secondary panels) ──── */
function showSecondarySkeletons() {
  const s = (n) => `<div class="skeleton-pulse" style="height:${n}px; margin-bottom:10px;"></div>`;
  const header = (icon, title) => `
    <div class="skeleton-card__header">
      <span style="font-size:1.1rem;">${icon}</span>
      <div class="skeleton-pulse" style="height:15px; width:52%; border-radius:6px;"></div>
      <div class="skeleton-pulse" style="height:20px; width:110px; border-radius:12px; margin-left:auto;"></div>
    </div>`;

  // ── Literature card ──
  const lit = document.getElementById('bw-literature-card');
  if (lit) {
    lit.dataset.skeleton = 'true';
    lit.style.display = 'block';
    lit.innerHTML = `
      ${header('📚', '')}
      <div class="skeleton-grid-2">
        <div class="skeleton-pulse" style="height:130px;"></div>
        <div class="skeleton-pulse" style="height:130px;"></div>
        <div class="skeleton-pulse" style="height:130px;"></div>
        <div class="skeleton-pulse" style="height:130px;"></div>
      </div>`;
  }

  // ── Timeline card ──
  const tl = document.getElementById('bw-timeline-card');
  if (tl) {
    tl.dataset.skeleton = 'true';
    tl.style.display = 'block';
    tl.innerHTML = `
      ${header('📈', '')}
      <div class="skeleton-grid-3">
        <div class="skeleton-pulse" style="height:72px;"></div>
        <div class="skeleton-pulse" style="height:72px;"></div>
        <div class="skeleton-pulse" style="height:72px;"></div>
      </div>
      <div class="skeleton-pulse" style="height:300px;"></div>
      ${s(14)}`;
  }

  // ── Violations card ──
  const viol = document.getElementById('bw-violations-card');
  if (viol) {
    viol.dataset.skeleton = 'true';
    viol.style.display = 'block';
    viol.innerHTML = `
      ${header('⚠️', '')}
      ${s(18)}
      <div class="skeleton-pulse" style="height:220px;"></div>
      <div style="display:flex; gap:0.75rem; margin-top:0.75rem;">
        <div class="skeleton-pulse" style="height:32px; width:160px;"></div>
        <div class="skeleton-pulse" style="height:14px; width:120px; margin-top:8px;"></div>
      </div>`;
  }

  // ── Bias card ──
  const bias = document.getElementById('bw-bias-card');
  if (bias) {
    bias.dataset.skeleton = 'true';
    bias.style.display = 'block';
    bias.innerHTML = `
      ${header('📊', '')}
      <div class="skeleton-pulse" style="height:280px;"></div>
      ${s(44)}`;
  }
}

function showSecondaryCardMessage(cardId, title, message) {
  const card = document.getElementById(cardId);
  if (!card) return;
  card.dataset.skeleton = '';
  card.style.display = 'block';
  card.innerHTML = `
    <div class="card-header">
      <h2 class="card-title">${title}</h2>
    </div>
    <div style="padding:1rem 0; color:#666; font-size:0.84rem; line-height:1.6;">
      ${message}
    </div>`;
}

function ensureLiteratureCardStructure(card) {
  if (!card || document.getElementById('bw-literature-body')) return;
  card.innerHTML = `
    <div class="card-header">
      <h2 class="card-title">📚 Literature Context — Real-World Impact of This Warning</h2>
      <span style="font-size:0.72rem; color:#888; align-self:center;">Based on pharmacovigilance literature + live FAERS data</span>
    </div>
    <div id="bw-literature-body" style="display:grid; grid-template-columns: repeat(2,1fr); gap:1rem; padding:0.5rem 0;"></div>`;
}

function ensureTimelineCardStructure(card) {
  if (!card || document.getElementById('bw-timeline-chart')) return;
  card.innerHTML = `
    <div class="card-header">
      <h2 class="card-title">📈 Before &amp; After Timeline — Annual FAERS Reports</h2>
      <span id="bw-timeline-badge"
        style="font-size:0.75rem; padding:3px 10px; border-radius:12px; background:#e3f2fd; color:#1565c0; font-weight:700; align-self:center;">
        Loading…
      </span>
    </div>
    <div id="bw-timeline-slopes" style="display:grid; grid-template-columns:repeat(3,1fr); gap:0.75rem; margin-bottom:1rem;"></div>
    <div class="chart-container" style="height:300px;">
      <canvas id="bw-timeline-chart"></canvas>
    </div>
    <div id="bw-timeline-interp" style="margin-top:0.75rem; font-size:0.8rem; color:#555; padding:8px 12px; background:#f8f9fa; border-radius:6px;"></div>`;
}

function ensureViolationsCardStructure(card) {
  if (!card || document.getElementById('bw-violations-table')) return;
  card.innerHTML = `
    <div class="card-header">
      <h2 class="card-title">⚠️ Post-Warning Reports of Labelled Events</h2>
      <span style="font-size:0.72rem; color:#888; align-self:center;">Stored in local pharmawatch.db</span>
    </div>
    <div id="bw-violations-summary" style="margin-bottom:0.75rem; font-size:0.83rem; color:#555;"></div>
    <div id="bw-violations-table" style="max-height:380px; overflow-y:auto;"></div>
    <div style="margin-top:0.75rem; display:flex; gap:0.75rem; flex-wrap:wrap; align-items:center;">
      <button id="bw-view-all-violations-btn" class="btn btn--sm btn--outline"
        style="font-size:0.78rem;">📋 View Entire DB Log</button>
      <span id="bw-db-log-status" style="font-size:0.75rem; color:#888;"></span>
    </div>
    <div id="bw-all-violations-log" style="display:none; margin-top:1rem;"></div>`;
}

function ensureBiasCardStructure(card) {
  if (!card || document.getElementById('bw-bias-chart')) return;
  card.innerHTML = `
    <div class="card-header">
      <h2 class="card-title">📊 Early Peak in Reporting — Weber-effect Pattern</h2>
      <span id="bw-weber-badge"
        style="font-size:0.75rem; padding:3px 10px; border-radius:12px; font-weight:700; align-self:center;">
        Analysing…
      </span>
    </div>
    <div class="chart-container" style="height:280px;">
      <canvas id="bw-bias-chart"></canvas>
    </div>
    <div id="bw-bias-note"
      style="margin-top:0.75rem; font-size:0.8rem; line-height:1.6; padding:10px 14px;
             background:#fff8e1; border-left:4px solid #f9a825; border-radius:6px; color:#333;">
    </div>`;
}

/* ──────────────── LITERATURE CARD RENDERER ────────────────── */
function renderLiteratureCard(drugName, eventsRes, violationsData, timelineData, biasData) {
  const card = document.getElementById('bw-literature-card');
  if (!card) return;
  ensureLiteratureCardStructure(card);
  const body = document.getElementById('bw-literature-body');
  if (!body) return;
  card.dataset.skeleton = '';

  const warnedPct = eventsRes.boxed_warning_percentage || 0;
  const violCount = violationsData?.violations_detected ?? '?';
  const postTotal = violationsData?.violations?.reduce((s, v) => s + (v.post_count || 0), 0) ?? 0;
  const preTotal  = violationsData?.violations?.reduce((s, v) => s + (v.pre_count  || 0), 0) ?? 0;
  const warningYear = violationsData?.warning_year ?? null;

  const weberDetected = biasData?.weber_peak_detected ?? false;
  const peakYear      = biasData?.peak_year ?? null;

  const preSlope  = timelineData?.pre_slope  ?? null;
  const postSlope = timelineData?.post_slope ?? null;
  const slopeDown = preSlope !== null && postSlope !== null && postSlope < preSlope;

  const pills = [
    {
      icon: '📉', label: 'A — Reports Over Time',
      color: '#37474f',
      bg:    '#eceff1',
      value: preSlope !== null
        ? `Reports per year changed by ${preSlope > 0 ? '+' : ''}${preSlope}/yr before ${warningYear} and by ${postSlope > 0 ? '+' : ''}${postSlope}/yr from ${warningYear} on.`
        : (warningYear === null
            ? 'No sourced warning year is on record for this drug, so no before/after comparison is shown.'
            : 'Too few years of data to compare slopes.'),
    },
    {
      icon: '⚠️', label: 'B — Post-Warning Reports of Labelled Events',
      color: '#37474f', bg: '#eceff1',
      value: warningYear === null
        ? (violationsData === null
            ? 'Data unavailable (backend may still be loading).'
            : `${violCount} event type(s) named in the warning were reported; no sourced warning year, so no before/after split.`)
        : `${violCount} labelled event type(s) with <strong>${postTotal.toLocaleString()}</strong> reports received from ${warningYear} onward.`,
    },
    {
      icon: '🔄', label: 'C — Share of Reports Naming a Labelled Event',
      color: '#1a237e', bg: '#e8eaf6',
      value: `${warnedPct}% of the reports for ${escHtml(drugName)} include an event term named in its boxed warning.`,
    },
    {
      icon: '📊', label: 'D — Early Peak in Reporting',
      color: weberDetected ? '#e65100' : '#33691e',
      bg:    weberDetected ? '#fff3e0'  : '#f1f8e9',
      value: weberDetected
        ? `Reports peaked early, in ${peakYear}. Peaks of this shape are described as the Weber effect, but counts alone cannot show why reporting changed.`
        : 'No early peak above the threshold in the reports available.',
    },
  ];

  body.innerHTML = pills.map(p => `
    <div style="padding:14px; background:${p.bg}; border-radius:8px; border-left:4px solid ${p.color};">
      <div style="font-size:1.3rem; margin-bottom:6px;">${p.icon}</div>
      <div style="font-size:0.78rem; font-weight:800; text-transform:uppercase; color:${p.color}; margin-bottom:4px;">${p.label}</div>
      <div style="font-size:0.8rem; line-height:1.55; color:#333;">${p.value}</div>
    </div>`).join('');

  card.dataset.skeleton = '';
  card.style.display = 'block';
  card.classList.remove('bw-section-reveal');
  void card.offsetWidth; // force reflow so animation re-triggers
  card.classList.add('bw-section-reveal');
}

/* ──────────────── TIMELINE CHART RENDERER ──────────────────── */
let bwTimelineChart = null;
function renderTimelineChart(data) {
  const card = document.getElementById('bw-timeline-card');
  if (!card) return;
  if (!data || data.error) {
    showSecondaryCardMessage(
      'bw-timeline-card',
      '📈 Before & After Timeline — Annual FAERS Reports',
      'Timeline data is unavailable right now. The backend did not return a usable FAERS time series for this drug.'
    );
    return;
  }
  ensureTimelineCardStructure(card);

  card.dataset.skeleton = '';
  card.style.display = 'block';
  card.classList.remove('bw-section-reveal');
  void card.offsetWidth;
  card.classList.add('bw-section-reveal');

  // Slope badges
  const slopesEl = document.getElementById('bw-timeline-slopes');
  if (slopesEl) {
    const pre  = data.pre_slope;
    const post = data.post_slope;
    const warningYr = data.warning_year ?? '—';
    const slopeColor = v => v === null ? '#888' : v > 0 ? '#c0392b' : '#2e7d32';
    slopesEl.innerHTML = `
      <div style="text-align:center; padding:10px; background:#f8f9fa; border-radius:8px;">
        <div style="font-size:1.4rem; font-weight:800; color:#003d7c;">${warningYr}</div>
        <div style="font-size:0.72rem; color:#666; text-transform:uppercase;">Warning Year</div>
      </div>
      <div style="text-align:center; padding:10px; background:#e8f5e9; border-radius:8px;">
        <div style="font-size:1.4rem; font-weight:800; color:${slopeColor(pre)};">${pre !== null ? (pre > 0 ? '+' : '') + pre : 'N/A'}</div>
        <div style="font-size:0.72rem; color:#666; text-transform:uppercase;">Pre-Warning Slope (reports/yr)</div>
      </div>
      <div style="text-align:center; padding:10px; background:${post < pre ? '#e8f5e9' : '#fef2f2'}; border-radius:8px;">
        <div style="font-size:1.4rem; font-weight:800; color:${slopeColor(post)};">${post !== null ? (post > 0 ? '+' : '') + post : 'N/A'}</div>
        <div style="font-size:0.72rem; color:#666; text-transform:uppercase;">Post-Warning Slope (reports/yr)</div>
      </div>`;
  }

  // Badge
  const badge = document.getElementById('bw-timeline-badge');
  if (badge) {
    if (data.pre_slope !== null && data.post_slope !== null) {
      const lower = data.post_slope < data.pre_slope;
      badge.textContent = lower ? '📉 Slower growth from the warning year' : '📈 Faster growth from the warning year';
      badge.style.background = '#eceff1';
      badge.style.color = '#37474f';
    } else {
      badge.textContent = data.warning_year == null ? 'No sourced warning year' : 'Slope data insufficient';
      badge.style.background = '#f5f5f5';
      badge.style.color = '#888';
    }
  }

  // Chart
  const ctx = document.getElementById('bw-timeline-chart');
  if (!ctx) return;
  if (bwTimelineChart) bwTimelineChart.destroy();

  const labels = data.labels;
  const counts = data.counts;
  const wIdx   = data.warning_index;

  // Build point colors — highlight warning year in red
  const ptColors = labels.map((_, i) => i === wIdx ? '#c0392b' : '#003d7c');
  const ptRadius = labels.map((_, i) => i === wIdx ? 8 : 4);

  bwTimelineChart = new Chart(ctx, {
    type: 'line',
    data: {
      labels,
      datasets: [{
        label: 'Annual FAERS Reports',
        data: counts,
        borderColor: '#003d7c',
        backgroundColor: 'rgba(0,61,124,0.07)',
        tension: 0.3, fill: true,
        pointBackgroundColor: ptColors,
        pointRadius: ptRadius,
        pointHoverRadius: 8,
      }]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: ctx2 => {
              const yr = labels[ctx2.dataIndex];
              const suffix = ctx2.dataIndex === wIdx ? ' ← Warning issued' : '';
              return ` ${ctx2.raw.toLocaleString()} reports${suffix}`;
            }
          }
        }
      },
      scales: {
        x: { ticks: { font: { size: 10 } } },
        y: { beginAtZero: true, title: { display: true, text: 'FAERS Reports' } }
      }
    }
  });

  const interp = document.getElementById('bw-timeline-interp');
  if (interp) interp.textContent = data.interpretation || '';
}

/* ──────────────── VIOLATIONS LOG RENDERER ──────────────────── */
function renderViolationsLog(drugName, data) {
  const card = document.getElementById('bw-violations-card');
  if (!card) return;
  if (!data || data.error) {
    showSecondaryCardMessage(
      'bw-violations-card',
      '⚠️ Post-Warning Reports of Labelled Events',
      'This analysis is unavailable right now. The backend did not return a usable post-warning report summary.'
    );
    return;
  }
  ensureViolationsCardStructure(card);
  const summary = document.getElementById('bw-violations-summary');
  const table   = document.getElementById('bw-violations-table');

  card.dataset.skeleton = '';
  card.style.display = 'block';
  card.classList.remove('bw-section-reveal');
  void card.offsetWidth;
  card.classList.add('bw-section-reveal');

  const violations = data.violations || [];
  const stored     = data.stored_violations || [];
  const hasYear = data.warning_year != null;
  const totalPost = violations.reduce((s, v) => s + (v.post_count || 0), 0);

  if (summary) {
    summary.innerHTML = data.has_boxed_warning === false
      ? `ℹ️ No boxed warning found for this drug, so there is nothing to compare.`
      : escHtml(data.message || '') + (hasYear ? ` Total reports received from <strong>${escHtml(String(data.warning_year))}</strong> onward: <strong>${totalPost.toLocaleString()}</strong>.` : '');
  }

  if (table) {
    if (violations.length === 0) {
      table.innerHTML = `<p style="color:#888; font-size:0.83rem; padding:1rem 0;">No event term named in the warning was among the most reported terms for this drug.</p>`;
    } else {
      table.innerHTML = `
        <table style="width:100%; font-size:0.8rem; border-collapse:collapse;">
          <thead><tr style="border-bottom:2px solid #ddd; text-align:left; background:#fef2f2;">
            <th style="padding:7px 8px;">Adverse Event</th>
            <th style="padding:7px 8px;" title="Reports received from the warning year onward">Reports From Warning Year ↓</th>
            <th style="padding:7px 8px;" title="Reports received before the warning year">Reports Before</th>
            <th style="padding:7px 8px;">% of All Reports</th>
            <th style="padding:7px 8px;">All Years</th>
          </tr></thead>
          <tbody>
            ${violations.map(v => `
              <tr style="border-bottom:1px solid #f0e0e0; background:${v.post_count > 0 ? '#fff9f9' : '#fff'};">
                <td style="padding:6px 8px; font-weight:600;">${escHtml(v.term)}</td>
                <td style="padding:6px 8px; font-weight:800;">${v.post_count == null ? '—' : v.post_count.toLocaleString()}</td>
                <td style="padding:6px 8px;">${v.pre_count == null ? '—' : v.pre_count.toLocaleString()}</td>
                <td style="padding:6px 8px;">${v.percentage}%</td>
                <td style="padding:6px 8px; color:#666;">${v.total_count.toLocaleString()}</td>
              </tr>`).join('')}
          </tbody>
        </table>
        <div style="margin-top:0.5rem; font-size:0.72rem; color:#888; font-style:italic;">
          Counts are reports received from 1 January of the sourced warning year. Report counts are not rates and depend on reporting behaviour.
        </div>`;
    }
  }

  // "View Entire DB Log" button
  const allBtn = document.getElementById('bw-view-all-violations-btn');
  const allLog = document.getElementById('bw-all-violations-log');
  const dbStatus = document.getElementById('bw-db-log-status');
  if (allBtn && allLog) {
    allBtn.onclick = async () => {
      if (allLog.style.display !== 'none') { allLog.style.display = 'none'; allBtn.textContent = '📋 View Entire DB Log'; return; }
      allBtn.textContent = '⏳ Loading…';
      try {
        const res = await fetch('/api/boxed-warning/post-warning-reports/all?limit=100').then(r => r.json());
        const rows = res.violations || [];
        if (dbStatus) dbStatus.textContent = `${rows.length} total records in pharmawatch.db`;
        allLog.innerHTML = rows.length === 0
          ? '<p style="color:#888; font-size:0.82rem;">No records stored yet.</p>'
          : `<div style="font-size:0.78rem; font-weight:700; margin-bottom:0.5rem; color:#c0392b;">All Stored Records (${rows.length})</div>
             <table style="width:100%; font-size:0.77rem; border-collapse:collapse;">
               <thead><tr style="border-bottom:2px solid #ddd; text-align:left;">
                 <th style="padding:5px 7px;">Drug</th>
                 <th style="padding:5px 7px;">Event</th>
                 <th style="padding:5px 7px;">Post-Warning</th>
                 <th style="padding:5px 7px;">Pre-Warning</th>
                 <th style="padding:5px 7px;">Queried At</th>
               </tr></thead>
               <tbody>
                 ${rows.map(r => `
                   <tr style="border-bottom:1px solid #eee;">
                     <td style="padding:5px 7px; text-transform:uppercase; font-weight:600;">${escHtml(r.drug)}</td>
                     <td style="padding:5px 7px;">${escHtml(r.adverse_event)}</td>
                     <td style="padding:5px 7px; color:#c0392b; font-weight:700;">${r.faers_count_post_warning.toLocaleString()}</td>
                     <td style="padding:5px 7px; color:#2e7d32;">${r.faers_count_pre_warning.toLocaleString()}</td>
                     <td style="padding:5px 7px; color:#888;">${r.queried_at ?? ''}</td>
                   </tr>`).join('')}
               </tbody>
             </table>`;
        allLog.style.display = 'block';
        allBtn.textContent = '▲ Hide DB Log';
      } catch (e) {
        allLog.innerHTML = `<p style="color:#c0392b; font-size:0.82rem;">Failed to load DB log: ${e.message}</p>`;
        allLog.style.display = 'block';
        allBtn.textContent = '📋 View Entire DB Log';
      }
    };
  }
}

/* ──────────────── WEBER EFFECT CHART RENDERER ──────────────── */
let bwBiasChart = null;
function renderBiasChart(data) {
  const card = document.getElementById('bw-bias-card');
  if (!card) return;
  if (!data || data.error) {
    showSecondaryCardMessage(
      'bw-bias-card',
      '📊 Early Peak in Reporting (Weber-effect pattern)',
      'This analysis is unavailable right now. The backend did not return a usable bias-analysis response.'
    );
    return;
  }
  ensureBiasCardStructure(card);
  const note  = document.getElementById('bw-bias-note');
  const badge = document.getElementById('bw-weber-badge');

  card.dataset.skeleton = '';
  card.style.display = 'block';
  card.classList.remove('bw-section-reveal');
  void card.offsetWidth;
  card.classList.add('bw-section-reveal');

  // ── Badge ──────────────────────────────────────────────────────────────────
  if (badge) {
    const methodLabels = {
      window:           'Sourced warning-year window',
      relative_pattern: 'Descriptive relative pattern',
    };
    const tierLabel = data.method ? ` · ${methodLabels[data.method] || data.method}` : '';
    if (data.weber_peak_detected) {
      badge.textContent = `Early peak ${data.peak_year}${tierLabel}`;
      badge.style.background = '#fff3e0';
      badge.style.color       = '#e65100';
    } else {
      badge.textContent = '✅ No Weber Peak';
      badge.style.background = '#e8f5e9';
      badge.style.color       = '#2e7d32';
    }
  }

  // ── Notoriety note ─────────────────────────────────────────────────────────
  if (note) note.textContent = data.notoriety_note || '';

  // ── Data coverage banner (shown when warning predates FAERS) ──────────────
  let coverageBanner = document.getElementById('bw-coverage-banner');
  if (data.faers_predates_warning) {
    if (!coverageBanner) {
      coverageBanner = document.createElement('div');
      coverageBanner.id = 'bw-coverage-banner';
      coverageBanner.style.cssText = `
        margin: 0.5rem 0 0.75rem;
        padding: 7px 12px;
        background: #fffde7;
        border-left: 4px solid #f9a825;
        border-radius: 6px;
        font-size: 0.78rem;
        color: #5d4037;
        line-height: 1.5;
      `;
      // Insert before the chart
      const chartWrap = document.querySelector('#bw-bias-card .chart-container');
      if (chartWrap) chartWrap.parentNode.insertBefore(coverageBanner, chartWrap);
    }
    const dataFirst = (data.labels || [])[0] || '?';
    coverageBanner.innerHTML = `
      ⚠️ <strong>Data coverage note:</strong>
      The recorded warning year is <strong>${escHtml(String(data.warning_year))}</strong>,
      but the reports available only begin at <strong>${escHtml(dataFirst)}</strong>.
      The chart shows available data (${escHtml(data.data_coverage || dataFirst + '–present')});
      the window around the warning cannot be examined.
    `;
  } else if (coverageBanner) {
    coverageBanner.remove();
  }

  // ── Bar chart ──────────────────────────────────────────────────────────────
  const ctx = document.getElementById('bw-bias-chart');
  if (!ctx) return;
  if (bwBiasChart) bwBiasChart.destroy();

  const labels      = data.labels || [];
  const counts      = data.counts || [];
  const warningYear = String(data.warning_year ?? '');
  const peakYear    = String(data.peak_year ?? '');

  // Color: peak bar = red, first-data bar (proxy anchor) = amber if warning predated FAERS,
  // warning-year bar = amber if it falls in the visible data, others = blue.
  const firstDataYear = labels[0] || '';
  const barColors = labels.map(yr => {
    if (yr === peakYear && data.weber_peak_detected) return '#c0392b';
    if (yr === warningYear) return '#f9a825';
    if (data.faers_predates_warning && yr === firstDataYear) return '#ffd54f'; // proxy anchor
    return '#3498db';
  });

  bwBiasChart = new Chart(ctx, {
    type: 'bar',
    data: {
      labels,
      datasets: [{
        label: 'Annual FAERS Reports',
        data: counts,
        backgroundColor: barColors,
        borderRadius: 4,
      }]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: ctx2 => {
              const yr = labels[ctx2.dataIndex];
              let suffix = '';
              if (yr === warningYear && !data.faers_predates_warning) suffix = ' ← Warning issued';
              if (yr === firstDataYear && data.faers_predates_warning)
                suffix = ` ← First FAERS record (warning was ${data.warning_year})`;
              if (yr === peakYear && data.weber_peak_detected) suffix = ' ← Weber Peak';
              return ` ${ctx2.raw.toLocaleString()} reports${suffix}`;
            }
          }
        }
      },
      scales: {
        x: { ticks: { font: { size: 10 } } },
        y: { beginAtZero: true, title: { display: true, text: 'FAERS Reports / Year' } }
      }
    }
  });
}

/* ──────────────── ALERT BANNER ────────────────────────────── */
function initAlertBanner() {
  document.getElementById('alert-close')?.addEventListener('click', () => {
    const banner = document.getElementById('alert-banner');
    if (banner) { banner.style.opacity = '0'; banner.style.transition = '0.3s'; setTimeout(() => banner.remove(), 300); }
  });
}




/* ──────────────── SYSTEM STATUS (real) ─────────────────────── */
async function updateSystemStatus() {
  const el = document.getElementById('system-status-dot');
  if (!el) return;
  try {
    const res = await fetch('/api/health', { cache: 'no-store' });
    const data = await res.json();
    const ok = res.ok && data.status === 'ok';
    el.textContent = ok ? '● Backend connected' : '● Backend degraded';
    el.style.color = ok ? '#00c853' : '#f0c040';
    el.title = data.database || '';
  } catch (e) {
    el.textContent = '● Backend unreachable';
    el.style.color = '#ef4444';
  }
}

/* ──────────────── UTILITIES ───────────────────────────────── */
function escHtml(str) {
  return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

/* ──────────────── BOOT ────────────────────────────────────── */
document.addEventListener('DOMContentLoaded', () => {
  initNavigation();
  initAlertBanner();
  initSignalsPage();
  initDrugSearch();
  initInteractionGraph();
  initBoxedWarnings();
  initPRRCalculator();
  initCounters();
  loadTopEvents();

  // Boot the dashboard section (default)
  navigateTo('dashboard');
  updateSystemStatus();
  setInterval(updateSystemStatus, 30000);

  // Boot the modular Javascript logic
  if (typeof MLModels !== 'undefined') MLModels.boot();
  if (typeof DistributedStorage !== 'undefined') DistributedStorage.boot();

  // Initialize tooltips on the illnesses
  setTimeout(() => {
    if (typeof ApiLayer !== 'undefined') ApiLayer.initTooltips();
  }, 500);

  // Pre-fetch real drug list from openFDA in background for autocomplete fallback
  if (typeof ApiLayer !== 'undefined') {
    ApiLayer.preloadDrugList().then(() => {
      console.log('[PharmaWatch] Drug list preloaded from openFDA');
    });
  }

  // Add style for stat-mini
  const style = document.createElement('style');
  style.textContent = `
    .stat-mini { text-align:center; padding:0.5rem; background:var(--gray-50); border-radius:8px; border:1px solid var(--gray-200); }
    .stat-mini__val { font-size:1.3rem; font-weight:800; color:var(--gray-900); }
    .stat-mini__lbl { font-size:0.7rem; color:var(--gray-600); text-transform:uppercase; margin-top:2px; }
    .prr-critical { color:var(--cdc-red) !important; }
    .prr-high { color:#e65100 !important; }
  `;
  document.head.appendChild(style);
});
