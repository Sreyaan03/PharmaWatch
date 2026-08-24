/**
 * Research Guide (Phase 0)
 * Pure frontend logic for the interactive glossary, workflow personas, and metric cards.
 * No external dependencies except standard DOM APIs.
 */

// ==========================================
// 1. DATA DEFINITIONS
// ==========================================

const GLOSSARY_TERMS = [
  {
    id: "prr",
    term: "Proportional Reporting Ratio (PRR)",
    plain: "A number that tells you how much more often a drug is linked to a side effect compared to all other drugs.",
    technical: "Ratio of the proportion of reports for drug X mentioning event Y versus the proportion of all other drug reports mentioning event Y.",
    formula: "PRR = [a / (a+b)] ÷ [c / (c+d)]",
    example: "Aspirin has a PRR of 3.4 for GI Bleeding → aspirin users are 3.4× more likely to appear in GI bleeding reports than users of other drugs.",
    links_to: "signals"
  },
  {
    id: "ror",
    term: "Reporting Odds Ratio (ROR)",
    plain: "Similar to PRR, but compares the odds of the event happening with the drug vs without the drug.",
    technical: "The odds of a specific event occurring with a specific drug, divided by the odds of the same event occurring with all other drugs.",
    formula: "ROR = (a × d) / (b × c)",
    example: "An ROR of 2.5 means the odds of reporting this side effect are 2.5 times higher when taking this drug.",
    links_to: "signals"
  },
  {
    id: "bcpnn",
    term: "Bayesian Confidence Propagation Neural Network (BCPNN IC)",
    plain: "A statistical method that reduces false alarms when dealing with very rare drugs or rare side effects.",
    technical: "Uses Bayesian statistics to calculate the Information Component (IC), shrinking volatile small-sample estimates toward a prior (usually 0).",
    formula: "IC = log2 ( Observed / Expected )",
    example: "If a drug only has 2 reports of a rare event, PRR might jump to 50, but IC will stay low, saying 'we need more data to be sure'.",
    links_to: "signals"
  },
  {
    id: "tto",
    term: "Time-To-Onset (TTO)",
    plain: "How many days it takes for a side effect to appear after starting the drug.",
    technical: "The delta in days between patient.drug.drugstartdate and patient.patientonsetdate in the FAERS database.",
    formula: "TTO = Onset Date - Start Date",
    example: "Anaphylaxis has a TTO of 0-1 days. Liver damage might have a TTO of 30-90 days.",
    links_to: "dashboard" // Later will link to Temporal tab
  },
  {
    id: "velocity",
    term: "Velocity Spike",
    plain: "A sudden, statistically significant increase in the number of reports for a drug side effect.",
    technical: "A month where the report count exceeds the 12-month rolling mean by at least 2 standard deviations (Z-score > 2).",
    formula: "Velocity Spike = Count(M) > Mean(M-12..M-1) + 2×StDev",
    example: "If a drug usually gets 10 reports a month, and suddenly gets 45 in October, it triggers a Velocity Spike alert.",
    links_to: "dashboard"
  },
  {
    id: "polypharmacy",
    term: "Polypharmacy",
    plain: "When a patient is taking multiple medications at the same time, increasing the risk of bad interactions.",
    technical: "The concurrent use of multiple medications by a patient. In this tool, defined as 2 to 5 interacting active ingredients.",
    formula: "Not Applicable",
    example: "A patient taking a statin, a blood thinner, and an antibiotic has a 3-drug polypharmacy profile.",
    links_to: "interactions"
  },
  {
    id: "bc",
    term: "Betweenness Centrality (BC)",
    plain: "Measures how much of a 'hub' a drug is. If a drug connects many other drugs that shouldn't be mixed, it has high BC.",
    technical: "The fraction of all shortest paths in the interaction graph that pass through a given drug node.",
    formula: "BC(v) = Σ [σ(s,t|v) / σ(s,t)]",
    example: "Warfarin has extremely high Betweenness Centrality because it interacts with almost every other drug family.",
    links_to: "interactions"
  },
  {
    id: "faers",
    term: "FAERS / openFDA",
    plain: "The FDA's massive public database where doctors and patients report side effects.",
    technical: "FDA Adverse Event Reporting System. The openFDA API provides JSON endpoints to query this data.",
    formula: "Not Applicable",
    example: "When you search a drug in PharmaWatch, it pulls live report counts directly from FAERS.",
    links_to: "dashboard"
  }
];

const WORKFLOW_PERSONAS = [
  {
    id: "researcher",
    name: "Drug Safety Researcher",
    steps: [
      { title: "Identify Signals", desc: "Start in Signal Detection to find PRR > 2.0", icon: "📊", link: "signals" },
      { title: "Check Clinical Notes", desc: "Upload a PDF in Dashboard to run BioBERT extraction", icon: "📄", link: "dashboard" },
      { title: "Analyze Deeply", desc: "Go to Drug Search to view MedDRA distributions", icon: "🔍", link: "drug-search" }
    ]
  },
  {
    id: "pharmacist",
    name: "Clinical Pharmacist",
    steps: [
      { title: "Review Patient Drugs", desc: "List all medications the patient is currently taking", icon: "📋", link: "interactions" },
      { title: "Run GNN Analyzer", desc: "Enter up to 5 drugs in Polypharmacy Risk Analyzer", icon: "⚗️", link: "interactions" },
      { title: "Check Boxed Warnings", desc: "Cross-reference the worst offenders in Boxed Warnings", icon: "⚠", link: "boxed-warnings" }
    ]
  },
  {
    id: "student",
    name: "Policy Analyst / Student",
    steps: [
      { title: "Learn Concepts", desc: "Read this Research Guide to understand PRR and FAERS", icon: "📖", link: "research-guide" },
      { title: "View Architecture", desc: "Look at the Pipeline Health demo on the Dashboard", icon: "⚙️", link: "dashboard" },
      { title: "Explore Top Events", desc: "See the highest reported events globally on Dashboard", icon: "🌍", link: "dashboard" }
    ]
  }
];

const METRIC_CARDS = [
  { acronym: "PRR", name: "Proportional Reporting Ratio", purpose: "Measures disproportionality of an adverse event.", formula: "PRR = [a / (a+b)] ÷ [c / (c+d)]", scale: "green: < 2.0, amber: 2.0-4.0, red: > 4.0", context: "FDA flags signals when PRR > 2 and count > 3.", color: "color-red" },
  { acronym: "ROR", name: "Reporting Odds Ratio", purpose: "Odds of the event with the drug vs without.", formula: "ROR = (a × d) / (b × c)", scale: "green: < 1.0, amber: 1.0-2.0, red: > 2.0", context: "Used by the Dutch pharmacovigilance centre Lareb.", color: "color-amber" },
  { acronym: "IC", name: "Information Component (BCPNN)", purpose: "Bayesian shrinkage to prevent false positives.", formula: "IC = log2 ( Obs / Exp )", scale: "green: < 0, amber: 0-1, red: > 1", context: "Used by the WHO Uppsala Monitoring Centre.", color: "color-blue" },
  { acronym: "TTO", name: "Time To Onset", purpose: "Days between starting a drug and the event.", formula: "Onset - Start Date", scale: "Varies by mechanism (acute vs chronic)", context: "Critical for establishing causality.", color: "color-green" }
];


// ==========================================
// 2. RENDER FUNCTIONS
// ==========================================

function renderGlossary() {
  const container = document.getElementById("guide-glossary-panel");
  if (!container) return;

  let html = `
    <h2 class="guide-section-title">Interactive Glossary</h2>
    <div class="guide-glossary-controls">
      <input type="text" id="guide-search" class="guide-glossary-search" placeholder="Search terms, acronyms, or formulas..." />
    </div>
    <div class="guide-glossary-grid" id="guide-glossary-grid">
  `;

  html += GLOSSARY_TERMS.map((term, index) => {
    // Expand the first 3 by default
    const expandedClass = index < 3 ? "expanded" : "";
    return `
      <div class="guide-card ${expandedClass}" data-term="${term.term.toLowerCase()} ${term.id.toLowerCase()}">
        <div class="guide-card-header">
          <h3 class="guide-card-title">${term.term}</h3>
          <button class="guide-card-badge" onclick="document.querySelector('[data-section=\\'${term.links_to}\\']').click()">Go to ${term.links_to.toUpperCase()} ↗</button>
        </div>
        <p class="guide-card-plain">${term.plain}</p>
        <button class="guide-card-toggle">View Technical Details</button>
        <div class="guide-card-details">
          <p><strong>Technical:</strong> ${term.technical}</p>
          <div class="guide-formula-box">${term.formula}</div>
          <p><strong>Example:</strong> ${term.example}</p>
        </div>
      </div>
    `;
  }).join('');

  html += `</div>`;
  container.innerHTML = html;

  // Add event listeners for toggles
  const toggles = container.querySelectorAll(".guide-card-toggle");
  toggles.forEach(toggle => {
    toggle.addEventListener("click", (e) => {
      const card = e.target.closest(".guide-card");
      card.classList.toggle("expanded");
    });
  });

  // Add event listener for search
  const searchInput = document.getElementById("guide-search");
  if (searchInput) {
    searchInput.addEventListener("input", (e) => {
      const query = e.target.value.toLowerCase();
      const cards = container.querySelectorAll(".guide-card");
      cards.forEach(card => {
        const text = card.getAttribute("data-term");
        if (text.includes(query)) {
          card.style.display = "flex";
        } else {
          card.style.display = "none";
        }
      });
    });
  }
}

function renderWorkflows() {
  const container = document.getElementById("guide-workflow-panel");
  if (!container) return;

  let html = `
    <h2 class="guide-section-title">How to Use PharmaWatch</h2>
    <div class="guide-workflow-tabs">
      ${WORKFLOW_PERSONAS.map((p, i) => `<button class="guide-persona-tab ${i === 0 ? 'active' : ''}" data-target="${p.id}">${p.name}</button>`).join('')}
    </div>
    <div id="guide-workflow-content">
      <!-- Populated dynamically -->
    </div>
  `;
  
  container.innerHTML = html;

  const contentDiv = document.getElementById("guide-workflow-content");
  
  const updateWorkflow = (id) => {
    const persona = WORKFLOW_PERSONAS.find(p => p.id === id);
    contentDiv.innerHTML = `
      <div class="guide-stepper">
        ${persona.steps.map((s, i) => `
          <div class="guide-step">
            <div class="guide-step-icon">${s.icon}</div>
            <div class="guide-step-title">Step ${i+1}: ${s.title}</div>
            <div class="guide-step-desc">${s.desc}</div>
            <button class="btn btn--sm btn--outline" style="color:var(--cdc-blue); border-color:var(--cdc-blue);" onclick="document.querySelector('[data-section=\\'${s.link}\\']').click()">Go to ${s.link} →</button>
          </div>
        `).join('')}
      </div>
    `;
  };

  updateWorkflow(WORKFLOW_PERSONAS[0].id);

  // Tabs events
  const tabs = container.querySelectorAll(".guide-persona-tab");
  tabs.forEach(tab => {
    tab.addEventListener("click", (e) => {
      tabs.forEach(t => t.classList.remove("active"));
      e.target.classList.add("active");
      updateWorkflow(e.target.getAttribute("data-target"));
    });
  });
}

function renderMetricCards() {
  const container = document.getElementById("guide-metrics-panel");
  if (!container) return;

  let html = `
    <h2 class="guide-section-title">Metric Cheat Sheet</h2>
    <div class="guide-metrics-grid">
  `;

  html += METRIC_CARDS.map(m => `
    <div class="guide-metric-card">
      <div class="guide-metric-card-top ${m.color}"></div>
      <div class="guide-metric-card-body">
        <h3 class="guide-metric-acronym">${m.acronym}</h3>
        <div class="guide-metric-name">${m.name}</div>
        <p style="font-size:0.85rem; margin-bottom:1rem; color:var(--gray-700);">${m.purpose}</p>
        <div class="guide-formula-box" style="font-size:0.8rem;">${m.formula}</div>
        <div style="font-size:0.8rem; margin-top:1rem;"><strong>Scale:</strong> ${m.scale}</div>
        <div style="font-size:0.8rem; margin-top:0.5rem; color:var(--gray-600);"><em>${m.context}</em></div>
      </div>
    </div>
  `).join('');

  html += `</div>`;
  container.innerHTML = html;
}

// ==========================================
// 4. DECISION FLOWCHART
// ==========================================
// Not implemented in version 1 of guide.js, will leave it empty.
// In the current guide plan, we omit this to save space/time as it was a complex SVG to generate.
function renderFlowchart() {
  const container = document.getElementById("guide-flowchart-panel");
  if (!container) return;
  
  // Optional: add a placeholder for future implementation
  container.innerHTML = `
    <h2 class="guide-section-title">Decision Guide</h2>
    <div class="guide-flowchart-wrapper">
      <p style="color:var(--gray-600); margin: var(--space-4) 0;"><em>Decision flowchart visualization is currently under development. Please refer to the "How to Use" workflows above.</em></p>
    </div>
  `;
}

// ==========================================
// 3. INITIALIZATION
// ==========================================

function initGuide() {
  console.log("[Guide] Initializing Research Guide...");
  renderWorkflows();
  renderMetricCards();
  renderGlossary();
  renderFlowchart();
}

// Automatically init if we're on the page (or when the tab is clicked)
document.addEventListener("DOMContentLoaded", () => {
  initGuide();
});
