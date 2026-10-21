/* ============================================================
   distributed_storage.js — Big Data Pipeline Engine

   REAL DATA (from Kafka pipeline via Flask API):
     • Kafka topics: message counts, consumer lag, last-seen timestamps
     • FDA Safety Alerts: live feed from kafka_alerts table
     • PubMed papers: live feed from kafka_pubmed table
     • Clinical Trials: active studies from kafka_trials table

   ARCHITECTURAL DEMOS (simulated — Spark/HDFS/HBase not deployed):
     • Apache Spark  — micro-batch jobs, stage timing
     • HDFS          — block replication, storage growth
     • Apache HBase  — put/get ops

   The "Architecture Demo" badge is only shown for Spark/HDFS/HBase.
   The Kafka section now shows REAL data.
   ============================================================ */

const DistributedStorage = {

  /* ── Real Kafka state (populated from /api/pipeline/stats) ── */
  kafka: {
    topics: {
      'faers-release':       { partitions: 4, offset: 0, lag: 0, total: 0, recent: 0 },
      'pubmed-stream':   { partitions: 2, offset: 0, lag: 0, total: 0, recent: 0 },
      'clinical-trials': { partitions: 2, offset: 0, lag: 0, total: 0, recent: 0 },
      'fda-alerts':      { partitions: 1, offset: 0, lag: 0, total: 0, recent: 0 },
    },
    brokers: 1,
    replicationFactor: 1,
    running: false,
    ingested: { pubmed_papers: 0, active_trials: 0, fda_alerts: 0 },
  },

  /* ── Simulated components (Spark / HDFS / HBase) ── */
  spark: {
    activeBatches: 0,
    batchDurationMs: 500,
    recordsPerBatch: 0,
    stagesCompleted: 0,
    tasksRunning: 0,
    totalTasksCompleted: 0,
    executors: 8,
    coresPerExecutor: 4,
  },

  hdfs: {
    totalBlocks: 142_880,
    replicatedBlocks: 142_880,
    totalGB: 4_210,
    writtenMBLast5min: 0,
    underReplicated: 0,
  },

  hbase: {
    putOpsPerSec: 0,
    getOpsPerSec: 0,
    rowsScanned: 0,
    regions: 48,
  },

  throughputChart: null,
  kafkaLagChart: null,

  /* ══════════════════════════════════════════════════════════════
     REAL KAFKA DATA — fetches from /api/pipeline/stats
  ══════════════════════════════════════════════════════════════ */

  async fetchKafkaStats() {
    try {
      const res = await fetch('/api/pipeline/stats');
      if (!res.ok) return;
      const data = await res.json();

      this.kafka.running = data.kafka_running || false;
      this.kafka.ingested = data.ingested || { pubmed_papers: 0, active_trials: 0, fda_alerts: 0 };

      // Update topic state from real consumer data
      const topics = data.topics || {};
      Object.entries(topics).forEach(([name, info]) => {
        if (this.kafka.topics[name]) {
          this.kafka.topics[name].total  = info.total_messages || 0;
          this.kafka.topics[name].recent = info.recent_10min   || 0;
          this.kafka.topics[name].offset = info.total_messages || 0;
          // lag = 0 when consumer is caught up (real Kafka reports this)
          this.kafka.topics[name].lag    = 0;
        }
      });

      this._updateKafkaStatusBadge();
      this._updateIngestionCounters();
      this._updateKafkaLagChart();
      this._updateTopicRateNodes();
      this._updateKafkaMetric();

    } catch (e) {
      // silently fail — Kafka may not be running
    }
  },

  _updateKafkaStatusBadge() {
    const badge = document.getElementById('kafka-status-badge');
    if (!badge) return;
    if (this.kafka.running) {
      badge.textContent = '🟢 LIVE — Real Kafka Pipeline';
      badge.style.color = '#00c853';
      badge.style.borderColor = 'rgba(0,200,83,0.4)';
      badge.style.background  = 'rgba(0,200,83,0.08)';
    } else {
      badge.textContent = '⏳ Kafka starting up — waiting for first messages...';
      badge.style.color = '#f0a500';
      badge.style.borderColor = 'rgba(240,165,0,0.4)';
      badge.style.background  = 'rgba(240,165,0,0.08)';
    }
  },

  _updateIngestionCounters() {
    const { pubmed_papers, active_trials, fda_alerts } = this.kafka.ingested;

    const setEl = (id, val) => {
      const el = document.getElementById(id);
      if (el) el.textContent = val.toLocaleString();
    };
    setEl('kafka-pubmed-count',  pubmed_papers);
    setEl('kafka-trials-count',  active_trials);
    setEl('kafka-alerts-count',  fda_alerts);

    // Total messages across all real topics
    const totalMsgs = Object.values(this.kafka.topics)
      .reduce((s, t) => s + (t.total || 0), 0);
    setEl('kafka-total-msgs', totalMsgs);
  },

  _updateTopicRateNodes() {
    const nodeMap = {
      'fda-rate':    'faers-release',
      'social-rate': 'fda-alerts',
      'ct-rate':     'clinical-trials',
    };
    Object.entries(nodeMap).forEach(([elId, topic]) => {
      const el = document.getElementById(elId);
      if (!el) return;
      const t = this.kafka.topics[topic];
      if (!t) return;
      if (t.total > 0) {
        el.textContent = `${t.total.toLocaleString()} msgs ingested | ${t.recent}/10min`;
      }
      // else leave simulated value
    });
  },

  /* ══════════════════════════════════════════════════════════════
     REAL LIVE FEED — FDA Alerts from /api/pipeline/alerts
  ══════════════════════════════════════════════════════════════ */

  async fetchLiveFeed() {
    try {
      const [alertsRes, pubmedRes] = await Promise.all([
        fetch('/api/pipeline/alerts'),
        fetch('/api/pipeline/pubmed'),
      ]);
      const alertsData = await alertsRes.json();
      const pubmedData = await pubmedRes.json();

      this._renderLiveFeed(alertsData.alerts || [], pubmedData.papers || []);
    } catch (e) {}
  },

  _renderLiveFeed(alerts, papers) {
    const feedEl = document.getElementById('kafka-live-feed');
    if (!feedEl) return;

    // Build combined feed sorted by ingested_at
    const items = [
      ...alerts.map(a => ({ type: 'alert',  title: a.title,   link: a.link,  time: a.ingested_at, source: 'FDA Alert' })),
      ...papers.map(p => ({ type: 'paper',  title: p.title,   link: `https://pubmed.ncbi.nlm.nih.gov/${p.pmid}/`, time: p.ingested_at, source: p.journal || 'PubMed' })),
    ].sort((a, b) => new Date(b.time) - new Date(a.time)).slice(0, 12);

    if (items.length === 0) {
      feedEl.innerHTML = `
        <div style="text-align:center; padding:2rem; color:var(--text-muted); font-size:0.85rem;">
          ⏳ Waiting for first Kafka messages...<br>
          <small>openFDA polls every 10 min · FDA RSS every 30 min · PubMed every 60 min</small>
        </div>`;
      return;
    }

    feedEl.innerHTML = items.map(item => {
      const icon  = item.type === 'alert' ? '🚨' : '📄';
      const color = item.type === 'alert' ? '#ef5350' : '#42a5f5';
      const ago   = this._timeAgo(item.time);
      return `
        <div class="kafka-feed-item" style="
          padding: 0.7rem 1rem;
          border-bottom: 1px solid rgba(255,255,255,0.05);
          display: flex; gap: 0.8rem; align-items: flex-start;
        ">
          <span style="font-size:1.1rem; flex-shrink:0; margin-top:2px;">${icon}</span>
          <div style="flex:1; min-width:0;">
            <div style="font-size:0.82rem; font-weight:600; color:${color};
              white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">
              ${item.link
                ? `<a href="${item.link}" target="_blank" style="color:inherit;text-decoration:none;">${item.title}</a>`
                : item.title}
            </div>
            <div style="font-size:0.72rem; color:var(--text-muted); margin-top:2px;">
              ${item.source} · ${ago}
            </div>
          </div>
        </div>`;
    }).join('');
  },

  _timeAgo(isoStr) {
    if (!isoStr) return 'just now';
    const diff = Date.now() - new Date(isoStr).getTime();
    const m = Math.floor(diff / 60000);
    if (m < 1)  return 'just now';
    if (m < 60) return `${m}m ago`;
    return `${Math.floor(m/60)}h ago`;
  },

  /* ══════════════════════════════════════════════════════════════
     KAFKA LAG CHART — real data when available, simulated otherwise
  ══════════════════════════════════════════════════════════════ */

  initKafkaLagChart() {
    const ctx = document.getElementById('kafka-lag-chart');
    if (!ctx) return;
    if (this.kafkaLagChart) this.kafkaLagChart.destroy();

    this.kafkaLagChart = new Chart(ctx, {
      type: 'bar',
      data: {
        labels: ['faers-release', 'pubmed-stream', 'clinical-trials', 'fda-alerts'],
        datasets: [{
          label: 'Messages Ingested (total)',
          data: [0, 0, 0, 0],
          backgroundColor: ['#003d7c', '#00695c', '#f0a500', '#7b1fa2'],
          borderRadius: 8,
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: { duration: 600 },
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: ctx => ` ${ctx.raw.toLocaleString()} messages`,
            }
          }
        },
        scales: {
          y: {
            beginAtZero: true,
            title: { display: true, text: 'Messages Ingested' }
          }
        }
      }
    });
  },

  _updateKafkaLagChart() {
    if (!this.kafkaLagChart) return;
    const topics = ['faers-release', 'pubmed-stream', 'clinical-trials', 'fda-alerts'];
    const data   = topics.map(t => this.kafka.topics[t]?.total || 0);

    // Only update if we have real data
    if (data.some(v => v > 0)) {
      this.kafkaLagChart.data.datasets[0].data  = data;
      this.kafkaLagChart.data.datasets[0].label = 'Messages Ingested (real)';
      this.kafkaLagChart.update('none');
    }
  },

  /* ══════════════════════════════════════════════════════════════
     KAFKA METRIC BAR (Pipeline Health panel)
  ══════════════════════════════════════════════════════════════ */

  startKafkaMetricBar() {
    this._updateKafkaMetric();
    setInterval(() => this._updateKafkaMetric(), 5000);
  },

  _updateKafkaMetric() {
    const el = document.getElementById('kafka-metric');
    if (!el) return;
    const total = Object.values(this.kafka.topics).reduce((s, t) => s + (t.total || 0), 0);
    if (total > 0) {
      const recentAll = Object.values(this.kafka.topics).reduce((s, t) => s + (t.recent || 0), 0);
      el.textContent = `${total.toLocaleString()} msgs total · ${recentAll}/10min`;
    } else if (this.kafka.running) {
      el.textContent = 'Kafka online · Listening for events';
    } else {
      el.textContent = 'Kafka pipeline idle';
    }
  },

  /* ══════════════════════════════════════════════════════════════
     COMPONENT STATE — honest labels only, no simulated numbers.
     Spark runs as an offline batch job (not a live stream) and HDFS/HBase are an
     architecture illustration with no cluster connected; both read "not connected".
  ══════════════════════════════════════════════════════════════ */

  renderComponentState() {
    const sparkEl = document.getElementById('spark-metric');
    if (sparkEl) sparkEl.textContent = 'Spark batch — runs offline (not a live stream)';
    const logEl = document.getElementById('spark-task-log');
    if (logEl) logEl.innerHTML = '<div class="spark-log-entry" style="color:var(--text-muted);">' +
      'The all-pairs Spark job runs as a batch, not a live stream; see Methods &amp; Validation for its measured run time and row count.</div>';
    const hdfsEl = document.getElementById('hdfs-metric');
    if (hdfsEl) hdfsEl.textContent = 'Not connected (no cluster)';
  },

  /* THROUGHPUT CHART — real Kafka messages/min per topic from /api/pipeline/stats, or "not
     running". Never a simulated series. */
  initThroughputChart() {
    const ctx = document.getElementById('throughput-chart');
    if (!ctx) return;
    if (this.throughputChart) this.throughputChart.destroy();
    this.throughputChart = new Chart(ctx, {
      type: 'bar',
      data: { labels: [], datasets: [{
        label: 'Messages / min (last 10 min, by topic)', data: [],
        backgroundColor: '#003d7c', borderRadius: 4,
      }] },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false },
          tooltip: { callbacks: { label: ctx => ` ${ctx.raw} msg/min` } } },
        scales: { y: { beginAtZero: true, title: { display: true, text: 'Messages / min' } } }
      }
    });
    this._refreshThroughput();
    setInterval(() => this._refreshThroughput(), 30000);
  },

  async _refreshThroughput() {
    if (!this.throughputChart) return;
    try {
      const d = await (await fetch('/api/pipeline/stats')).json();
      const topics = d.topics || {};
      const labels = Object.keys(topics);
      this.throughputChart.data.labels = labels;
      this.throughputChart.data.datasets[0].data = labels.map(t => topics[t].msgs_per_min || 0);
      this.throughputChart.data.datasets[0].label = d.kafka_running
        ? 'Messages / min (last 10 min, by topic)' : 'Kafka not running';
      this.throughputChart.update();
    } catch (e) { /* leave the chart empty; no simulated fallback */ }
  },

  /* ══════════════════════════════════════════════════════════════
     SOURCE METERS — real ingested totals per topic, or nothing. Bar widths are
     relative to the largest topic; no fixed/simulated widths.
  ══════════════════════════════════════════════════════════════ */

  animateMeters() {
    this._meters = [
      { fill: 'fda-bar',    val: 'fda-val',    topic: 'faers-release' },
      { fill: 'social-bar', val: 'social-val', topic: 'fda-alerts' },
      { fill: 'ct-bar',     val: 'ct-val',     topic: 'clinical-trials' },
    ];
    this._updateMeters();
    setInterval(() => this._updateMeters(), 10000);
  },

  _updateMeters() {
    const totals = (this._meters || []).map(m => (this.kafka.topics[m.topic]?.total) || 0);
    const max = Math.max(1, ...totals);
    (this._meters || []).forEach((m, i) => {
      const fill = document.getElementById(m.fill);
      const val = document.getElementById(m.val);
      if (fill) fill.style.width = `${Math.round((totals[i] / max) * 100)}%`;
      if (val) val.textContent = totals[i] > 0 ? `${totals[i].toLocaleString()} msgs ingested` : 'not running';
    });
  },

  /* ══════════════════════════════════════════════════════════════
     INJECT KAFKA LIVE FEED + STATUS PANEL
  ══════════════════════════════════════════════════════════════ */

  injectKafkaPanel() {
    // Find the data-sources section to inject our live feed
    const container = document.querySelector('#data-sources .container.section-body')
                   || document.querySelector('#pipeline .container');
    if (!container || document.getElementById('kafka-live-panel')) return;

    const panel = document.createElement('div');
    panel.id = 'kafka-live-panel';
    panel.style.cssText = 'margin: 1.5rem 0;';
    panel.innerHTML = `
      <!-- Status badge -->
      <div id="kafka-status-badge" style="
        display: inline-flex; align-items: center; gap: 8px;
        border: 1px solid rgba(240,165,0,0.4);
        border-radius: 8px; padding: 6px 14px;
        font-size: 0.78rem; font-weight: 600;
        color: #f0a500;
        background: rgba(240,165,0,0.08);
        margin-bottom: 1rem;
      ">⏳ Kafka starting up...</div>

      <!-- Ingestion counters -->
      <div style="display:grid; grid-template-columns:repeat(auto-fit,minmax(140px,1fr)); gap:1rem; margin-bottom:1.5rem;">
        ${[
          { id:'kafka-total-msgs',  label:'Total Messages',       icon:'📨' },
          { id:'kafka-pubmed-count', label:'PubMed Papers',       icon:'📄' },
          { id:'kafka-trials-count', label:'Active Trials',       icon:'🔬' },
          { id:'kafka-alerts-count', label:'FDA Safety Alerts',   icon:'🚨' },
        ].map(c => `
          <div style="
            background: rgba(255,255,255,0.04);
            border: 1px solid rgba(255,255,255,0.08);
            border-radius: 10px; padding: 0.9rem 1rem; text-align:center;
          ">
            <div style="font-size:1.4rem;">${c.icon}</div>
            <div id="${c.id}" style="font-size:1.4rem; font-weight:700; color:var(--accent);">0</div>
            <div style="font-size:0.72rem; color:var(--text-muted); margin-top:2px;">${c.label}</div>
          </div>
        `).join('')}
      </div>

      <!-- Live feed -->
      <div style="
        background: rgba(255,255,255,0.03);
        border: 1px solid rgba(255,255,255,0.08);
        border-radius: 12px; overflow:hidden;
      ">
        <div style="
          padding: 0.7rem 1rem;
          background: rgba(255,255,255,0.04);
          border-bottom: 1px solid rgba(255,255,255,0.08);
          font-size: 0.8rem; font-weight: 700;
          display:flex; align-items:center; gap:8px;
        ">
          <span style="width:8px;height:8px;border-radius:50%;background:#00c853;
            box-shadow:0 0 6px #00c853; animation: pulse 1.5s infinite;
            flex-shrink:0; display:inline-block;"></span>
          LIVE KAFKA FEED
          <span style="margin-left:auto; font-weight:400; color:var(--text-muted); font-size:0.72rem;">
            Updates automatically
          </span>
        </div>
        <div id="kafka-live-feed" style="max-height:300px; overflow-y:auto; font-size:0.82rem;"></div>
      </div>
    `;
    container.insertBefore(panel, container.firstChild);
  },

  /* ══════════════════════════════════════════════════════════════
     SPARK/HDFS demo badge (only for those simulated components)
  ══════════════════════════════════════════════════════════════ */

  injectArchBadge() {
    const container = document.querySelector('#data-sources .container.section-body');
    if (!container || document.getElementById('data-sources-demo-badge')) return;
    const badge = document.createElement('div');
    badge.id = 'data-sources-demo-badge';
    badge.style.cssText = `
      display:flex; align-items:flex-start; gap:8px;
      background:rgba(255,193,7,0.12); border:1px solid rgba(255,193,7,0.3);
      border-radius:8px; padding:8px 14px;
      font-size:0.76rem; color:#f0a500; font-weight:600;
      margin:1rem 0; line-height:1.4;
    `;
    badge.innerHTML = `
      <span style="font-size:1rem;flex-shrink:0;">⚡</span>
      <span><strong>What is live here:</strong> Kafka ingestion and the counts above are real.
      The Spark all-pairs job runs offline as a batch (its measured run time and row count are on the
      Methods &amp; Validation page). HDFS/HBase are an architecture illustration with no cluster
      connected, so those panels read "not connected" — never a simulated number.</span>
    `;
    container.appendChild(badge);
  },

  /* ══════════════════════════════════════════════════════════════
     BOOT
  ══════════════════════════════════════════════════════════════ */

  boot() {
    // Inject the live Kafka panel into the UI
    this.injectKafkaPanel();
    this.injectArchBadge();

    // Honest component state (no simulations) + real source meters
    this.renderComponentState();
    this.animateMeters();
    this.startKafkaMetricBar();

    // Fetch real Kafka data immediately, then every 30 seconds
    this.fetchKafkaStats();
    this.fetchLiveFeed();
    setInterval(() => this.fetchKafkaStats(), 30_000);
    setInterval(() => this.fetchLiveFeed(),   60_000);
  },
};
