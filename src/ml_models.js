/* ============================================================
   ml_models.js — model demo panels (biomedical NER + LSTM)
   Drives the Methodology demo panels; they call the real backend endpoints.
   ============================================================ */

function escapeHtml(text) {
  const el = document.createElement('div');
  el.textContent = String(text ?? '');
  return el.innerHTML;
}

const MLModels = {

  lstmDemoChart: null,

  /**
   * Initiates the visual counter for BioBERT entities extraction rate
   */
  startBioBERTSimulation() {
    const SAMPLE = "Patient reported severe nausea and dizziness after taking Aspirin and Metformin.";

    const updateThroughput = async () => {
      const el = document.getElementById('nlp-throughput');
      if (!el) return;

      // Check if the real BioBERT server is running
      const online = await BioBERT.isOnline().catch(() => false);

      if (online) {
        try {
          // Use a real measurement from the model
          const entitiesPerSec = await BioBERT.measureThroughput(SAMPLE);
          const entitiesPerMin = entitiesPerSec * 60;
          el.textContent = `${entitiesPerMin.toLocaleString()} entities/min`;
        } catch {
          // Server was online but errored — show a neutral fallback
          el.textContent = 'BioBERT error — check server';
        }
      } else {
        // Server not running: there is no throughput to report
        el.textContent = 'BioBERT not running';
      }
    };

    // Run immediately, then every 10 seconds
    updateThroughput();
    setInterval(updateThroughput, 10000);
  },

  /**
   * Creates the "Methodology" section interactive LSTM diagram
   * Shows historical baseline prediction contrasted against "real-time" streaming spikes
   */
  async initLSTMDemoChart(drug = 'Metformin') {
    const ctx = document.getElementById('lstm-demo-chart');
    const statusMsg = document.getElementById('lstm-status-msg');
    if (!ctx) return;

    if (this.lstmDemoChart) this.lstmDemoChart.destroy();
    if (statusMsg) statusMsg.textContent = `Loading LSTM data for ${drug}…`;

    try {
      const res = await fetch(`/api/lstm?drug=${encodeURIComponent(drug)}`);
      const data = await res.json();

      if (data.error) {
        if (statusMsg) { statusMsg.textContent = `❌ Error: ${data.error}`; statusMsg.style.color = '#c0392b'; }
        return;
      }

      const staleAlert = ctx.parentElement.querySelector('.lstm-rising-alert');
      if (staleAlert) staleAlert.remove();

      if (data.source === 'no_data') {
        if (statusMsg) { statusMsg.textContent = `ℹ️ ${data.message}`; statusMsg.style.color = '#e65100'; }
        return;
      }

      // Status: which model is shown and its error against the baseline, on held-out months
      if (statusMsg) {
        const used = data.model_used === 'lstm' ? 'LSTM' : 'Seasonal-naive baseline';
        const lstmNote = {
          trained: '',
          not_trained: ' · LSTM not trained (click 🧠 Train Model)',
          stale: ' · the trained LSTM is out of date for the loaded data; retrain',
          insufficient_data: ' · too few months to train an LSTM',
        }[data.lstm_status] || '';
        const lstmErr = data.lstm_error != null ? `, LSTM ${data.lstm_error.toFixed(3)}` : '';
        statusMsg.innerHTML = `<strong>${drug}</strong> · ${used} · mean absolute error over the last 12 months: ` +
          `${data.model_error.toFixed(3)} points (baseline ${data.baseline_error.toFixed(3)}${lstmErr})${lstmNote}`;
        statusMsg.style.color = data.model_used === 'lstm' ? '#2e7d32' : '#555';
      }

      this.lstmDemoChart = new Chart(ctx, {
        type: 'line',
        data: {
          labels: data.labels,
          datasets: [
            {
              label: data.model_used === 'lstm' ? 'LSTM forecast (held-out months)' : 'Seasonal-naive forecast (held-out months)',
              data: data.baseline,
              borderColor: '#003d7c', borderDash: [6, 3], tension: 0.3, fill: false,
              pointRadius: 0, borderWidth: 2, spanGaps: false
            },
            {
              label: 'Upper 95% interval',
              data: data.upper,
              borderColor: 'rgba(0,61,124,0.35)', borderWidth: 1, fill: false, pointRadius: 0, spanGaps: false
            },
            {
              label: 'Lower 95% interval',
              data: data.lower,
              borderColor: 'rgba(0,61,124,0.35)', backgroundColor: 'rgba(0,61,124,0.07)',
              borderWidth: 1, fill: '-1', pointRadius: 0, spanGaps: false
            },
            {
              label: 'Actual share of all reports',
              data: data.actual,
              borderColor: '#c0392b', backgroundColor: 'rgba(192,57,43,0.10)',
              tension: 0.3, fill: false, pointRadius: 3, pointHoverRadius: 6
            }
          ]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          interaction: { mode: 'index', intersect: false },
          plugins: {
            legend: { position: 'bottom', labels: { boxWidth: 12, font: { size: 11 } } }
          },
          scales: {
            x: { ticks: { maxTicksLimit: 10, font: { size: 10 } } },
            y: { beginAtZero: true, title: { display: true, text: 'Share of all reports received (%)' } }
          }
        }
      });

      // Is the latest share above its forecast interval? (descriptive, with registered trials)
      try {
        const gapRes = await fetch(`/api/lstm/gap-scan?drug=${encodeURIComponent(drug)}`);
        const gap = await gapRes.json();
        if (!gap.error) {
          const flagged = gap.run_reported;
          const alertEl = document.createElement('div');
          alertEl.className = 'lstm-rising-alert';
          alertEl.style.cssText = `margin-top:10px; padding:10px 14px;
            background:${flagged ? '#fff8e1' : '#f5f5f5'};
            border-left:3px solid ${flagged ? '#e65100' : '#9e9e9e'};
            font-size:0.8rem; border-radius:4px; line-height:1.6;`;
          const ct = gap.clinicaltrials || {};
          alertEl.innerHTML = `
            <div style="margin-bottom:4px;"><strong>${flagged ? 'Reporting share above the forecast interval' : 'Reporting share'}</strong></div>
            <div>${escapeHtml(gap.message || '')}</div>
            ${ct.note ? `<div style="margin-top:6px; font-size:0.77rem; color:#555;">🧪 <strong>ClinicalTrials.gov:</strong> ${escapeHtml(ct.note)}</div>` : ''}`;
          ctx.parentElement.appendChild(alertEl);
        }
      } catch (gapErr) {
        // gap scan unavailable; the chart stands on its own
      }

    } catch (e) {
      console.warn('LSTM fetch failed — backend may be offline:', e.message);
    }
  },


  /**
   * Wires up the drug search input, autocomplete, Load and Train buttons
   * on the Methodology page LSTM card.
   */
  initLSTMDrugSelector() {
    const input    = document.getElementById('lstm-drug-input');
    const suggs    = document.getElementById('lstm-drug-suggestions');
    const loadBtn  = document.getElementById('lstm-load-btn');
    const trainBtn = document.getElementById('lstm-train-inline-btn');
    const statusMsg = document.getElementById('lstm-status-msg');
    if (!input) return;

    let searchTimeout = null;
    let currentDrug = 'Metformin';
    input.value = currentDrug;

    // Autocomplete
    input.addEventListener('input', () => {
      const q = input.value.trim();
      if (!q || q.length < 2) { suggs.style.display = 'none'; return; }
      clearTimeout(searchTimeout);
      suggs.innerHTML = `<li style="color:#888; padding:8px 12px;">Searching…</li>`;
      suggs.style.display = 'block';
      searchTimeout = setTimeout(async () => {
        if (typeof ApiLayer === 'undefined') return;
        const matches = await ApiLayer.searchDrugNames(q);
        if (!matches.length) {
          suggs.innerHTML = `<li style="color:#888; padding:8px 12px;">No results for "${q}"</li>`;
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
      currentDrug = li.dataset.drug;
      suggs.style.display = 'none';
    });

    loadBtn?.addEventListener('click', () => {
      suggs.style.display = 'none';
      currentDrug = input.value.trim() || currentDrug;
      this.initLSTMDemoChart(currentDrug);
    });

    input.addEventListener('keydown', e => {
      if (e.key === 'Enter') {
        suggs.style.display = 'none';
        currentDrug = input.value.trim() || currentDrug;
        this.initLSTMDemoChart(currentDrug);
      }
      if (e.key === 'Escape') suggs.style.display = 'none';
    });

    trainBtn?.addEventListener('click', async () => {
      currentDrug = input.value.trim() || currentDrug;
      if (!currentDrug) { if (statusMsg) statusMsg.textContent = 'Enter a drug name first.'; return; }
      if (statusMsg) {
        statusMsg.innerHTML = `⏳ Training LSTM for <strong>${currentDrug}</strong>…`;
        statusMsg.style.color = '#e65100';
      }
      trainBtn.disabled = true;
      try {
        const r = await fetch(
          `/api/lstm/train?drug=${encodeURIComponent(currentDrug)}`,
          { method: 'POST' }
        );
        const d = await r.json();
        if (d.status === 'ok') {
          if (statusMsg) statusMsg.innerHTML = `✅ ${d.message} Loading chart…`;
          setTimeout(() => this.initLSTMDemoChart(currentDrug), 400);
        } else {
          if (statusMsg) { statusMsg.textContent = `❌ ${d.error}`; statusMsg.style.color = '#c0392b'; }
        }
      } catch (err) {
        if (statusMsg) { statusMsg.textContent = `❌ Backend offline: ${err.message}`; statusMsg.style.color = '#c0392b'; }
      }
      trainBtn.disabled = false;
    });

    document.addEventListener('click', e => {
      if (!e.target.closest('#lstm-drug-input') && !e.target.closest('#lstm-drug-suggestions')) {
        suggs.style.display = 'none';
      }
    });
  },

  /**
   * Triggers the ML simulations during app boot
   */
  boot() {
    this.startBioBERTSimulation();
  }
};
