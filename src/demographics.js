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

function initDemographicsTab() {
  const form = document.getElementById("demo-search-form");
  if (form) {
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      runDemographicsAnalysis();
    });
  }

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
    const [sexRes, ageRes, geoRes] = await Promise.all([
      fetch(`/api/demographics/sex?drug=${encodeURIComponent(drug)}&event=${encodeURIComponent(event)}`).then(r => r.json()),
      fetch(`/api/demographics/age?drug=${encodeURIComponent(drug)}&event=${encodeURIComponent(event)}`).then(r => r.json()),
      fetch(`/api/demographics/geo?drug=${encodeURIComponent(drug)}&event=${encodeURIComponent(event)}`).then(r => r.json())
    ]);

    renderSexChart(sexRes);
    renderAgeChart(ageRes);
    renderGeoChart(geoRes);
    renderDemographicsSummaryCards(sexRes, ageRes, geoRes);

    if (statusEl) {
      statusEl.style.display = "none";
    }
  } catch (err) {
    console.error("[Demographics Error]", err);
    if (statusEl) {
      statusEl.innerHTML = `Failed to load demographic data: ${err.message}`;
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
          data: [data.female_count, data.male_count, data.unknown_count || 0],
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
      labels: data.groups,
      datasets: [{
        label: 'Subgroup Case Count',
        data: data.counts,
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

  const fullLabels = data.countries.map(c => COUNTRY_NAMES[c] || c);

  demoCharts.geo = new Chart(canvas, {
    type: 'bar',
    data: {
      labels: fullLabels,
      datasets: [{
        label: 'Reporting Volume by Country',
        data: data.counts,
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

function renderDemographicsSummaryCards(sex, age, geo) {
  const elSex = document.getElementById("demo-card-sex");
  const elAge = document.getElementById("demo-card-age");
  const elGeo = document.getElementById("demo-card-geo");

  if (elSex) {
    elSex.innerHTML = `<div class="stat-card"><div class="stat-title">Sex Risk Stratification</div><div class="stat-value">${sex.higher_risk_group.toUpperCase()} RISK</div><div class="stat-desc">Risk Ratio (F/M): ${sex.sex_risk_ratio} (${sex.female_count} Female / ${sex.male_count} Male / ${sex.unknown_count || 0} Unknown)</div></div>`;
  }

  if (elAge) {
    elAge.innerHTML = `<div class="stat-card"><div class="stat-title">Highest-Risk Age Group</div><div class="stat-value">${age.highest_risk_group.toUpperCase()}</div><div class="stat-desc">Subgroup represents peak reporting vulnerability.</div></div>`;
  }

  if (elGeo) {
    const topFull = COUNTRY_NAMES[geo.top_country] || geo.top_country || 'United States (US)';
    elGeo.innerHTML = `<div class="stat-card"><div class="stat-title">Top Reporting Region</div><div class="stat-value">${topFull}</div><div class="stat-desc">Highest geographical concentration of co-reports.</div></div>`;
  }
}

// Bind load event
document.addEventListener("DOMContentLoaded", () => {
  initDemographicsTab();
});
