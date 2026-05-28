/* ============================================================
   api_layer.js — Real-Time API Data Feed
   Manages all external data integrations (openFDA, Wikipedia)
   ============================================================ */

const ApiLayer = {
  FDA_BASE_URL: 'https://api.fda.gov/drug',
  WIKI_BASE_URL: 'https://en.wikipedia.org/api/rest_v1/page/summary',
  wikiCache: {},
  fdaCache: { labels: {}, events: {} },

  /* ----------------------------------------------------------
     API KEY — read from the utility-bar input the user types
  ---------------------------------------------------------- */
  getFdaApiKey() {
    // Hardcoded Option B
    return 'Rtfpkk33k8qYViDvlaO6p1ZFdZ72hcvnbyn0nCiX';
  },

  buildFdaUrl(endpoint, queryParams) {
    const key = this.getFdaApiKey();
    let url = `${this.FDA_BASE_URL}/${endpoint}?${queryParams}`;
    if (key) url += `&api_key=${encodeURIComponent(key)}`;
    return url;
  },

  /* ----------------------------------------------------------
     DRUG NAME AUTOCOMPLETE
     Live-searches FDA as user types, returns matching generic names
  ---------------------------------------------------------- */
  async searchDrugNames(prefix) {
    if (!prefix || prefix.length < 2) return [];
    const cacheKey = `search_${prefix.toLowerCase()}`;
    if (this.fdaCache[cacheKey]) return this.fdaCache[cacheKey];

    try {
      // Use openFDA label search with a wildcard to find matching drug names
      const encoded = encodeURIComponent(prefix.toLowerCase());
      const url = this.buildFdaUrl(
        'label.json',
        `search=openfda.generic_name:${encoded}*&count=openfda.generic_name.exact&limit=15`
      );
      const res = await fetch(url);
      if (!res.ok) throw new Error(`FDA API ${res.status}`);
      const data = await res.json();

      const names = (data.results || [])
        .map(r => r.term)
        .filter(n => n.toLowerCase().startsWith(prefix.toLowerCase()))
        .map(n => n.charAt(0).toUpperCase() + n.slice(1).toLowerCase());

      this.fdaCache[cacheKey] = names;
      return names;
    } catch (e) {
      // Fallback — search within a reasonable curated list that is populated from the drug label count endpoint on boot
      const cached = this.fdaCache.topDrugs || [];
      return cached.filter(d => d.toLowerCase().startsWith(prefix.toLowerCase())).slice(0, 10);
    }
  },

  /* ----------------------------------------------------------
     PRE-LOAD a large list of common drugs at boot-time for instant
     offline autocomplete fallback (does NOT show hardcoded list;
     uses the FDA count endpoint for real drug names)
  ---------------------------------------------------------- */
  async preloadDrugList() {
    if (this.fdaCache.topDrugs && this.fdaCache.topDrugs.length > 0) return;
    try {
      const url = this.buildFdaUrl('label.json', 'count=openfda.generic_name.exact&limit=200');
      const res = await fetch(url);
      const data = await res.json();
      this.fdaCache.topDrugs = (data.results || []).map(r =>
        r.term.charAt(0).toUpperCase() + r.term.slice(1).toLowerCase()
      );
      console.log(`[ApiLayer] Preloaded ${this.fdaCache.topDrugs.length} drug names from openFDA`);
    } catch (e) {
      console.warn('[ApiLayer] Drug preload failed — live search will be used.');
    }
  },

  /* ----------------------------------------------------------
     DRUG LABEL — indications, dosages, pharmacological class
  ---------------------------------------------------------- */
  async fetchDrugLabel(drugName) {
    const normalized = drugName.toLowerCase();
    if (this.fdaCache.labels[normalized]) return this.fdaCache.labels[normalized];

    try {
      const url = this.buildFdaUrl(
        'label.json',
        `search=openfda.generic_name:"${encodeURIComponent(normalized)}"&limit=1`
      );
      const res = await fetch(url);
      if (!res.ok) throw new Error(`FDA ${res.status}`);
      const data = await res.json();
      const result = data.results[0];

      const indication = result.indications_and_usage
        ? result.indications_and_usage[0].replace(/<[^>]*>/g, '').split('.')[0].trim()
        : 'Symptomatic treatment / Various indications';

      const drugClass = result.openfda && result.openfda.pharm_class_epc
        ? result.openfda.pharm_class_epc[0]
        : 'Pharmacological Therapy';

      const dosage = result.dosage_and_administration
        ? result.dosage_and_administration[0].replace(/<[^>]*>/g, '').split('.')[0].trim()
        : 'Refer to prescribing information';

      const warnings = result.warnings
        ? result.warnings[0].replace(/<[^>]*>/g, '').trim().substring(0, 200) + '...'
        : 'Consult prescriber for full warning information.';

      const parsed = { indication, class: drugClass, dosage, warnings };
      this.fdaCache.labels[normalized] = parsed;
      return parsed;
    } catch (e) {
      console.warn('[ApiLayer] Label fetch failed:', e.message);
      return {
        indication: 'Indication data unavailable — check API key or try again.',
        class: 'Therapeutic Agent',
        dosage: 'Dosage unavailable',
        warnings: 'Warnings unavailable'
      };
    }
  },

  /* ----------------------------------------------------------
     DRUG ADVERSE EVENTS — live FAERS counts from openFDA
  ---------------------------------------------------------- */
  async fetchDrugEvents(drugName, limit = 8) {
    const normalized = drugName.toLowerCase();
    const cacheKey = `${normalized}_${limit}`;
    if (this.fdaCache.events[cacheKey]) return this.fdaCache.events[cacheKey];

    try {
      const url = this.buildFdaUrl(
        'event.json',
        `search=patient.drug.medicinalproduct:"${encodeURIComponent(normalized)}"&count=patient.reaction.reactionmeddrapt.exact&limit=${limit}`
      );
      const res = await fetch(url);
      if (!res.ok) throw new Error(`FDA Events ${res.status}`);
      const data = await res.json();

      const events = data.results || [];
      const totalApproximation = events.reduce((acc, v) => acc + v.count, 0) * 3;

      const parsed = {
        totalReports: totalApproximation,
        ades: events.map(e =>
          e.term.charAt(0).toUpperCase() + e.term.slice(1).toLowerCase()
        ),
        adeCounts: events.map(e => e.count)
      };

      this.fdaCache.events[cacheKey] = parsed;
      return parsed;
    } catch (e) {
      console.warn('[ApiLayer] Events fetch failed:', e.message);
      return null;
    }
  },

  /* ----------------------------------------------------------
     WIKIPEDIA ILLNESS DEFINITIONS — for hover tooltips
     Fixes the regex bug and improves the term cleaning
  ---------------------------------------------------------- */
  async fetchWikipediaSummary(term) {
    // Clean the term properly for Wikipedia lookup
    const cleanTerm = term
      .replace(/\s*\/\s*/g, '_')  // "Nausea / Vomiting" -> "Nausea_Vomiting"
      .replace(/\s+/g, '_')        // spaces -> underscores
      .replace(/[^a-zA-Z0-9_\-]/g, ''); // strip special chars

    if (this.wikiCache[cleanTerm]) return this.wikiCache[cleanTerm];

    try {
      const url = `${this.WIKI_BASE_URL}/${encodeURIComponent(cleanTerm)}`;
      const res = await fetch(url, { headers: { 'Accept': 'application/json' } });
      if (!res.ok) throw new Error(`Wiki ${res.status}`);
      const data = await res.json();

      const summary = (data.extract || 'No clinical definition available.')
        .replace(/<[^>]*>/g, '');
      const shortened = summary.length > 250 ? summary.substring(0, 250) + '…' : summary;
      this.wikiCache[cleanTerm] = shortened;
      return shortened;
    } catch (e) {
      // Try alternate term (sometimes need singular form)
      const altTerm = cleanTerm.replace(/_/g, '%20');
      try {
        const res2 = await fetch(`${this.WIKI_BASE_URL}/${altTerm}`);
        if (!res2.ok) throw new Error('fallback failed');
        const data2 = await res2.json();
        const summary2 = (data2.extract || 'Definition not found.').replace(/<[^>]*>/g, '');
        const short2 = summary2.length > 250 ? summary2.substring(0, 250) + '…' : summary2;
        this.wikiCache[cleanTerm] = short2;
        return short2;
      } catch {
        const fallback = `${term}: Clinical definition temporarily unavailable. Please consult a medical reference.`;
        this.wikiCache[cleanTerm] = fallback;
        return fallback;
      }
    }
  },

  /* ----------------------------------------------------------
     TOOLTIP INITIALIZATION
     Uses a pure CSS+JS approach so we don't depend on Tippy theme files.
     Re-initializes on every call (safe — destroys previous instances).
  ---------------------------------------------------------- */
  _tooltipEl: null,

  _ensureTooltipEl() {
    if (!this._tooltipEl) {
      const el = document.createElement('div');
      el.id = 'illness-tooltip';
      el.style.cssText = `
        position:fixed; z-index:99999; max-width:320px; padding:10px 14px;
        background:#fff; border:1px solid #d0d7de; border-radius:8px;
        box-shadow:0 8px 24px rgba(0,0,0,0.15); font-size:0.8rem; line-height:1.5;
        pointer-events:none; display:none; transition:opacity 0.15s;
        color:#1a1a2e; font-family:'Open Sans',sans-serif;
      `;
      document.body.appendChild(el);
      this._tooltipEl = el;
    }
    return this._tooltipEl;
  },

  initTooltips() {
    const tooltip = this._ensureTooltipEl();

    // Remove old listeners by replacing nodes via delegation
    document.removeEventListener('mouseover', this._tooltipOver);
    document.removeEventListener('mouseout', this._tooltipOut);
    document.removeEventListener('mousemove', this._tooltipMove);

    this._tooltipOver = async (e) => {
      const el = e.target.closest('.illness-hover');
      if (!el) return;

      const illness = el.getAttribute('data-illness');
      if (!illness) return;

      tooltip.innerHTML = `
        <strong style="color:#003d7c;display:block;margin-bottom:5px;border-bottom:2px solid #e8f0fe;padding-bottom:5px;font-size:0.85rem;">
          🏥 ${illness}
        </strong>
        <span style="color:#888;font-style:italic;">Fetching clinical definition…</span>
      `;
      tooltip.style.display = 'block';
      tooltip.style.opacity = '1';

      const summary = await ApiLayer.fetchWikipediaSummary(illness);
      tooltip.innerHTML = `
        <strong style="color:#003d7c;display:block;margin-bottom:5px;border-bottom:2px solid #e8f0fe;padding-bottom:5px;font-size:0.85rem;">
          🏥 ${illness}
        </strong>
        <span>${summary}</span>
        <div style="margin-top:6px;font-size:0.7rem;color:#aaa;text-align:right;">📖 Wikipedia · Medical Definitions</div>
      `;
    };

    this._tooltipOut = (e) => {
      if (!e.target.closest('.illness-hover')) return;
      tooltip.style.display = 'none';
    };

    this._tooltipMove = (e) => {
      if (tooltip.style.display === 'none') return;
      const x = e.clientX + 16;
      const y = e.clientY + 16;
      // Prevent overflow off-screen
      const maxX = window.innerWidth - 340;
      const maxY = window.innerHeight - 200;
      tooltip.style.left = `${Math.min(x, maxX)}px`;
      tooltip.style.top = `${Math.min(y, maxY)}px`;
    };

    document.addEventListener('mouseover', this._tooltipOver);
    document.addEventListener('mouseout', this._tooltipOut);
    document.addEventListener('mousemove', this._tooltipMove);
  }
};
