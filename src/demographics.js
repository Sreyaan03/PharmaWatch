/**
 * Patient Stratification & Demographics (Phase 2 / Feature 3)
 * Handles sex-stratified PRR, age subgroup distributions, and geographic reporting density (GRR).
 */

const COUNTRY_NAMES = {
  "US": "United States (US)",
  "GB": "United Kingdom (GB)",
  "CA": "Canada (CA)",
  "DE": "Germany (DE)",
  "FR": "France (FR)",
  "JP": "Japan (JP)",
  "IT": "Italy (IT)",
  "ES": "Spain (ES)",
  "AU": "Australia (AU)",
  "BR": "Brazil (BR)",
  "IN": "India (IN)",
  "CN": "China (CN)",
  "MX": "Mexico (MX)",
  "NL": "Netherlands (NL)",
  "CH": "Switzerland (CH)"
};

let demoCharts = {
  sex: null,
  age: null,
  geo: null
};

let demographicsInitialized = false;

function initDemographicsTab() {
  if (demographicsInitialized) {
    runDemographicsAnalysis();
    return;
  }
  demographicsInitialized = true;

  const form = document.getElementById("demo-search-form");
  const drugInput = document.getElementById("demo-drug-input");
  const eventInput = document.getElementById("demo-event-input");
  const suggs = document.getElementById("demo-drug-suggestions");

  if (form) {
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      runDemographicsAnalysis();
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
      runDemographicsAnalysis();
    });

    document.addEventListener("click", e => {
      if (!drugInput.contains(e.target) && !suggs.contains(e.target)) {
        suggs.style.display = "none";
      }
    });
  }

  // Bind Preset Chips
  document.querySelectorAll(".demo-chip").forEach(btn => {
    btn.addEventListener("click", () => {
      if (drugInput && btn.dataset.drug) drugInput.value = btn.dataset.drug;
      if (eventInput && btn.dataset.event) eventInput.value = btn.dataset.event;
      runDemographicsAnalysis();
    });
  });

  // Initial load with default drug/event
  runDemographicsAnalysis();
}

async function runDemographicsAnalysis() {
  const drugInput = document.getElementById("demo-drug-input");
  const eventInput = document.getElementById("demo-event-input");

  const drug = drugInput ? drugInput.value.trim() || "Metformin" : "Metformin";
  const event = eventInput ? eventInput.value.trim() || "Nausea" : "Nausea";

  const statusEl = document.getElementById("demo-status-banner");
  if (statusEl) {
    statusEl.innerHTML = `<span class="spinner"></span> Computing demographic risk stratification for <strong>${drug}</strong> + <strong>${event}</strong>...`;
    statusEl.className = "alert-banner alert-banner--info";
    statusEl.style.display = "block";
  }

  try {
    const fetchJson = async (url) => {
      const res = await fetch(url);
      if (!res.ok) {
        throw new Error(`API HTTP ${res.status}`);
      }
      return res.json();
    };

    const [sexRes, ageRes, geoRes] = await Promise.all([
      fetchJson(`/api/demographics/sex?drug=${encodeURIComponent(drug)}&event=${encodeURIComponent(event)}`),
      fetchJson(`/api/demographics/age?drug=${encodeURIComponent(drug)}&event=${encodeURIComponent(event)}`),
      fetchJson(`/api/demographics/geo?drug=${encodeURIComponent(drug)}&event=${encodeURIComponent(event)}`)
    ]);

    const totalReports = Math.max(
      sexRes.total_reports || 0,
      ageRes.total_reports || 0,
      geoRes.total_reports || 0
    );

    if (totalReports === 0) {
      if (statusEl) {
        statusEl.innerHTML = `ℹ No FAERS reports found for <strong>${drug}</strong> + <strong>${event}</strong>. Try one of the quick presets below.`;
        statusEl.className = "alert-banner alert-banner--warning";
        statusEl.style.display = "block";
      }
    } else if (statusEl) {
      statusEl.style.display = "none";
    }

    renderSexChart(sexRes);
    renderAgeChart(ageRes);
    renderGeoChart(geoRes);
    renderDemographicsSummaryCards(sexRes, ageRes, geoRes);
  } catch (err) {
    console.error("[Demographics Error]", err);
    if (statusEl) {
      statusEl.innerHTML = `Failed to load demographic data: ${err.message}. Please check backend connection.`;
      statusEl.className = "alert-banner alert-banner--critical";
    }
  }
}

function renderSexChart(data) {
  const canvas = document.getElementById("demo-sex-chart");
  if (!canvas) return;

  if (demoCharts.sex) {
    demoCharts.sex.destroy();
  }

  demoCharts.sex = new Chart(canvas, {
    type: 'bar',
    data: {
      labels: ['Female (F)', 'Male (M)', 'Unknown (U)'],
      datasets: [
        {
          label: 'FAERS Report Count',
          data: [data.female_count || 0, data.male_count || 0, data.unknown_count || 0],
          backgroundColor: ['#e91e63', '#0070c0', '#78909c'],
          borderRadius: 4
        }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      scales: {
        y: { beginAtZero: true, title: { display: true, text: 'FAERS Cases' } }
      }
    }
  });
}

function renderAgeChart(data) {
  const canvas = document.getElementById("demo-age-chart");
  if (!canvas) return;

  if (demoCharts.age) {
    demoCharts.age.destroy();
  }

  demoCharts.age = new Chart(canvas, {
    type: 'bar',
    data: {
      labels: data.groups || [],
      datasets: [{
        label: 'Subgroup Case Count',
        data: data.counts || [],
        backgroundColor: ['#26a69a', '#003d7c', '#c0392b'],
        borderRadius: 4
      }]
    },
    options: {
      indexAxis: 'y',
      responsive: true,
      maintainAspectRatio: false,
      scales: {
        x: { beginAtZero: true, title: { display: true, text: 'FAERS Case Count' } }
      }
    }
  });
}

function renderGeoChart(data) {
  const canvas = document.getElementById("demo-geo-chart");
  if (!canvas) return;

  if (demoCharts.geo) {
    demoCharts.geo.destroy();
  }

  const countries = data.countries || [];
  const fullLabels = countries.map(c => COUNTRY_NAMES[c] || c);

  demoCharts.geo = new Chart(canvas, {
    type: 'bar',
    data: {
      labels: fullLabels,
      datasets: [{
        label: 'Reporting Volume by Country',
        data: data.counts || [],
        backgroundColor: '#00695c',
        borderRadius: 4
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      scales: {
        y: { beginAtZero: true, title: { display: true, text: 'Total FAERS Reports' } },
        x: { title: { display: true, text: 'Country' } }
      }
    }
  });
}

function escapeDemoHtml(text) {
  const el = document.createElement('div');
  el.textContent = String(text ?? '');
  return el.innerHTML;
}

function renderDemographicsSummaryCards(sex, age, geo) {
  const elSex = document.getElementById("demo-card-sex");
  const elAge = document.getElementById("demo-card-age");
  const elGeo = document.getElementById("demo-card-geo");

  if (elSex) {
    const counts = `${sex.female_count || 0} Female / ${sex.male_count || 0} Male / ${sex.unknown_count || 0} Unknown`;
    if (sex.source === 'no_data' || !sex.higher_risk_group) {
      elSex.innerHTML = `<div class="stat-card"><div class="stat-title">Sex Risk Stratification</div><div class="stat-value">NOT AVAILABLE</div><div class="stat-desc">${sex.source === 'no_data' ? 'Too few reports to compare.' : 'Intervals overlap.'} (${counts})</div></div>`;
    } else if (sex.higher_risk_group === 'No difference shown') {
      elSex.innerHTML = `<div class="stat-card"><div class="stat-title">Sex Risk Stratification</div><div class="stat-value">NO DIFFERENCE SHOWN</div><div class="stat-desc">PRR intervals for women and men overlap. (${counts})</div></div>`;
    } else {
      elSex.innerHTML = `<div class="stat-card"><div class="stat-title">Sex Risk Stratification</div><div class="stat-value">${escapeDemoHtml(sex.higher_risk_group).toUpperCase()} HIGHER</div><div class="stat-desc">PRR ratio (F/M): ${sex.sex_risk_ratio != null ? sex.sex_risk_ratio : 'n/a'} (${counts})</div></div>`;
    }
  }

  if (elAge) {
    if (age.highest_risk_group) {
      elAge.innerHTML = `<div class="stat-card"><div class="stat-title">Highest-Risk Age Group</div><div class="stat-value">${escapeDemoHtml(age.highest_risk_group).toUpperCase()}</div><div class="stat-desc">Its PRR interval lies entirely above the other groups'.</div></div>`;
    } else {
      elAge.innerHTML = `<div class="stat-card"><div class="stat-title">Highest-Risk Age Group</div><div class="stat-value">NONE SHOWN</div><div class="stat-desc">${age.source === 'no_data' ? 'Too few reports to compare.' : 'No group has an interval clearly above the others.'}</div></div>`;
    }
  }

  if (elGeo) {
    const topFull = geo.top_country ? (COUNTRY_NAMES[geo.top_country] || geo.top_country) : 'NOT AVAILABLE';
    elGeo.innerHTML = `<div class="stat-card"><div class="stat-title">Top Reporting Country</div><div class="stat-value">${escapeDemoHtml(topFull)}</div><div class="stat-desc">Largest share of reports; reflects where reports are filed, not where risk is higher.</div></div>`;
  }
}

// Bind load event
document.addEventListener("DOMContentLoaded", () => {
  initDemographicsTab();
});
