/**
 * Temporal Analysis (Phase 1 / Feature 2)
 * Handles Time-to-Onset (TTO) distributions, report velocity spikes, and seasonality indexes.
 */

let temporalCharts = {
  velocity: null,
  tto: null,
  seasonality: null
};

function initTemporalTab() {
  const form = document.getElementById("temporal-search-form");
  const drugInput = document.getElementById("temporal-drug-input");
  const eventInput = document.getElementById("temporal-event-input");
  const suggs = document.getElementById("temporal-drug-suggestions");

  if (form) {
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      runTemporalAnalysis();
    });
  }

  // Live Autocomplete for Drug Input
  if (drugInput && suggs) {
    let searchTimeout = null;

    drugInput.addEventListener("input", () => {
      const q = drugInput.value.trim();
      if (!q || q.length < 2) {
        suggs.style.display = "none";
        return;
      }

      clearTimeout(searchTimeout);
      suggs.innerHTML = `<li style="color:#888; padding:8px 12px;">Searching openFDA…</li>`;
      suggs.style.display = "block";

      searchTimeout = setTimeout(async () => {
        if (typeof ApiLayer === 'undefined') return;
        const matches = await ApiLayer.searchDrugNames(q);
        if (matches.length === 0) {
          suggs.innerHTML = `<li style="color:#888; padding:8px 12px;">No results for "${q}"</li>`;
        } else {
          suggs.innerHTML = matches.slice(0, 8).map(d =>
            `<li role="option" data-drug="${d}" style="padding:8px 12px; cursor:pointer;">${d}</li>`
          ).join('');
        }
        suggs.style.display = "block";
      }, 250);
    });

    suggs.addEventListener("click", e => {
      const li = e.target.closest("li[data-drug]");
      if (!li) return;
      drugInput.value = li.dataset.drug;
      suggs.style.display = "none";
      runTemporalAnalysis();
    });

    document.addEventListener("click", e => {
      if (!drugInput.contains(e.target) && !suggs.contains(e.target)) {
        suggs.style.display = "none";
      }
    });
  }

  // Bind Preset Chips
  document.querySelectorAll(".temporal-chip").forEach(btn => {
    btn.addEventListener("click", () => {
      if (drugInput && btn.dataset.drug) drugInput.value = btn.dataset.drug;
      if (eventInput && btn.dataset.event) eventInput.value = btn.dataset.event;
      runTemporalAnalysis();
    });
  });

  // Run analysis if tab is active
  runTemporalAnalysis();
}

async function runTemporalAnalysis() {
  const drugInput = document.getElementById("temporal-drug-input");
  const eventInput = document.getElementById("temporal-event-input");

  const drug = drugInput ? drugInput.value.trim() || "Metformin" : "Metformin";
  const event = eventInput ? eventInput.value.trim() || "Nausea" : "Nausea";

  const statusEl = document.getElementById("temporal-status-banner");
  if (statusEl) {
    statusEl.innerHTML = `<span class="spinner"></span> Fetching temporal pattern data for <strong>${drug}</strong> + <strong>${event}</strong>...`;
    statusEl.className = "alert-banner alert-banner--info";
    statusEl.style.display = "block";
  }

  try {
    const [velRes, ttoRes, seasonRes] = await Promise.all([
      fetch(`/api/temporal/velocity?drug=${encodeURIComponent(drug)}&event=${encodeURIComponent(event)}`).then(r => r.json()),
      fetch(`/api/temporal/tto?drug=${encodeURIComponent(drug)}&event=${encodeURIComponent(event)}`).then(r => r.json()),
      fetch(`/api/temporal/seasonality?drug=${encodeURIComponent(drug)}&event=${encodeURIComponent(event)}`).then(r => r.json())
    ]);

    renderVelocityChart(velRes);
    renderTTOChart(ttoRes);
    renderSeasonalityChart(seasonRes);
    renderTemporalSummaryCards(velRes, ttoRes, seasonRes);

    if (statusEl) {
      statusEl.style.display = "none";
    }
  } catch (err) {
    console.error("[Temporal Analysis Error]", err);
    if (statusEl) {
      statusEl.innerHTML = `Failed to load temporal data: ${err.message}`;
      statusEl.className = "alert-banner alert-banner--critical";
    }
  }
}

function renderVelocityChart(data) {
  const canvas = document.getElementById("temporal-velocity-chart");
  if (!canvas) return;

  if (temporalCharts.velocity) {
    temporalCharts.velocity.destroy();
  }

  const pointColors = (data.spikes || []).map(s => s ? '#c0392b' : '#0070c0');
  const pointRadii = (data.spikes || []).map(s => s ? 8 : 4);

  temporalCharts.velocity = new Chart(canvas, {
    type: 'line',
    data: {
      labels: data.months || [],
      datasets: [{
        label: 'Monthly FAERS Reports',
        data: data.counts || [],
        borderColor: '#003d7c',
        backgroundColor: 'rgba(0, 61, 124, 0.08)',
        fill: true,
        tension: 0.2,
        pointBackgroundColor: pointColors,
        pointBorderColor: pointColors,
        pointRadius: pointRadii
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        tooltip: {
          callbacks: {
            afterLabel: function(ctx) {
              const idx = ctx.dataIndex;
              const isSpike = data.spikes ? data.spikes[idx] : false;
              const z = data.z_scores ? data.z_scores[idx] : 0;
              return `Z-score: ${z}${isSpike ? ' (VELOCITY SPIKE DETECTED)' : ''}`;
            }
          }
        }
      },
      scales: {
        y: { beginAtZero: true, title: { display: true, text: 'Report Count' } },
        x: { title: { display: true, text: 'Month (YYYYMM)' } }
      }
    }
  });
}

function renderTTOChart(data) {
  const canvas = document.getElementById("temporal-tto-chart");
  if (!canvas) return;

  if (temporalCharts.tto) {
    temporalCharts.tto.destroy();
  }

  temporalCharts.tto = new Chart(canvas, {
    type: 'bar',
    data: {
      labels: data.buckets || [],
      datasets: [{
        label: 'Estimated Reports',
        data: data.counts || [],
        backgroundColor: '#26a69a',
        borderRadius: 4
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      scales: {
        y: { beginAtZero: true, title: { display: true, text: 'Report Count' } },
        x: { title: { display: true, text: 'Time-to-Onset Interval' } }
      }
    }
  });
}

function renderSeasonalityChart(data) {
  const canvas = document.getElementById("temporal-seasonality-chart");
  if (!canvas) return;

  if (temporalCharts.seasonality) {
    temporalCharts.seasonality.destroy();
  }

  const indices = data.seasonality_index || [];
  const barColors = indices.map(si => si >= 1.15 ? '#f0a500' : '#0070c0');

  temporalCharts.seasonality = new Chart(canvas, {
    type: 'bar',
    data: {
      labels: data.months || [],
      datasets: [
        {
          type: 'line',
          label: 'Baseline (1.0)',
          data: Array(12).fill(1.0),
          borderColor: '#c0392b',
          borderDash: [5, 5],
          borderWidth: 2,
          pointRadius: 0
        },
        {
          label: 'Seasonality Index (SI)',
          data: indices,
          backgroundColor: barColors,
          borderRadius: 4
        }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      scales: {
        y: { beginAtZero: true, title: { display: true, text: 'Seasonality Index (1.0 = Mean)' } }
      }
    }
  });
}

function renderTemporalSummaryCards(vel, tto, season) {
  const elVelocity = document.getElementById("temp-card-velocity");
  const elTTO = document.getElementById("temp-card-tto");
  const elSeason = document.getElementById("temp-card-season");

  if (elVelocity) {
    elVelocity.innerHTML = vel.has_spike ?
      `<div class="stat-card stat-card--red"><div class="stat-title">Velocity Spike</div><div class="stat-value">ALERT</div><div class="stat-desc">Z-score > 2.0 detected in recent months.</div></div>` :
      `<div class="stat-card"><div class="stat-title">Velocity Status</div><div class="stat-value">STABLE</div><div class="stat-desc">No sudden report surge detected.</div></div>`;
  }

  if (elTTO) {
    elTTO.innerHTML = `<div class="stat-card"><div class="stat-title">Peak Time-To-Onset</div><div class="stat-value">${tto.peak_bucket || 'N/A'}</div><div class="stat-desc">Median onset ~${tto.median_days || 0} days after start.</div></div>`;
  }

  if (elSeason) {
    const elevated = season.elevated_months || [];
    const text = season.is_seasonal ? `Elevated in ${elevated.join(', ')}` : 'Uniform across months';
    elSeason.innerHTML = `<div class="stat-card"><div class="stat-title">Seasonality</div><div class="stat-value">${season.is_seasonal ? 'CYCLICAL' : 'NORMAL'}</div><div class="stat-desc">${text}</div></div>`;
  }
}

// Automatically bind tab init when loaded
document.addEventListener("DOMContentLoaded", () => {
  initTemporalTab();
});
