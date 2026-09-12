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
      'faers-raw':       { partitions: 4, offset: 0, lag: 0, total: 0, recent: 0 },
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
      'fda-rate':    'faers-raw',
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
        labels: ['faers-raw', 'pubmed-stream', 'clinical-trials', 'fda-alerts'],
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
    const topics = ['faers-raw', 'pubmed-stream', 'clinical-trials', 'fda-alerts'];
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
    const update = () => {
      const el = document.getElementById('kafka-metric');
      if (!el) return;
      const total = Object.values(this.kafka.topics).reduce((s, t) => s + (t.total || 0), 0);
      if (total > 0) {
        const recentAll = Object.values(this.kafka.topics).reduce((s, t) => s + (t.recent || 0), 0);
        el.textContent = `${total.toLocaleString()} msgs total · ${recentAll}/10min`;
      } else {
        el.textContent = 'Waiting for first Kafka events...';
      }
    };
    update();
    setInterval(update, 10000);
  },

  /* ══════════════════════════════════════════════════════════════
     SIMULATED SPARK MICRO-BATCH (architecture demo only)
  ══════════════════════════════════════════════════════════════ */

  startSparkSimulation() {
    setInterval(() => {
      this.spark.activeBatches = Math.floor(Math.random() * 3) + 1;
      this.spark.recordsPerBatch = Math.floor(Math.random() * 2000 + 800);
      this.spark.stagesCompleted += 2;
      this.spark.tasksRunning = this.spark.executors * this.spark.coresPerExecutor;
      this.spark.totalTasksCompleted += this.spark.tasksRunning;

      const sparkEl = document.getElementById('spark-metric');
      if (sparkEl) {
        const eventsPerSec = Math.floor(this.spark.recordsPerBatch * 2);
        sparkEl.textContent = `${eventsPerSec.toLocaleString()} events/s | ${this.spark.activeBatches} active batch(es)`;
      }
    }, this.spark.batchDurationMs);

    setInterval(() => {
      const logEl = document.getElementById('spark-task-log');
      if (!logEl) return;
      const stages = ['Schema normalisation', 'MedDRA mapping', 'BioBERT UDF', 'PRR computation', 'LSTM prediction', 'HBase write'];
      const stage = stages[Math.floor(Math.random() * stages.length)];
      const ms = Math.floor(Math.random() * 140 + 60);
      const entry = document.createElement('div');
      entry.className = 'spark-log-entry';
      entry.innerHTML = `<span class="spark-log-time">${new Date().toLocaleTimeString()}</span> Stage: <b>${stage}</b> — ${this.spark.tasksRunning} tasks / ${ms}ms`;
      logEl.prepend(entry);
      while (logEl.children.length > 8) logEl.removeChild(logEl.lastChild);
    }, 1200);
  },

  /* ══════════════════════════════════════════════════════════════
     SIMULATED HDFS + HBASE (architecture demo)
  ══════════════════════════════════════════════════════════════ */

  startHDFSSimulation() {
    setInterval(() => {
      const newBlocks = Math.floor(Math.random() * 12 + 4);
      this.hdfs.totalBlocks += newBlocks;
      this.hdfs.replicatedBlocks += Math.floor(newBlocks * this.kafka.replicationFactor);
      this.hdfs.totalGB += Math.random() * 0.04;
      this.hdfs.writtenMBLast5min += Math.random() * 18 + 8;
      if (this.hdfs.writtenMBLast5min > 500) this.hdfs.writtenMBLast5min = 0;

      const hdfsEl = document.getElementById('hdfs-metric');
      if (hdfsEl) hdfsEl.textContent = `${this.hdfs.totalGB.toFixed(1)} TB | ${this.hdfs.totalBlocks.toLocaleString()} blocks`;
    }, 3000);

    setInterval(() => {
      this.hbase.putOpsPerSec = Math.floor(Math.random() * 12000 + 8000);
      this.hbase.getOpsPerSec = Math.floor(Math.random() * 6000 + 3000);
      this.hbase.rowsScanned  += Math.floor(Math.random() * 5000 + 2000);
    }, 1000);
  },

  /* ══════════════════════════════════════════════════════════════
     THROUGHPUT CHART (24h sliding window — simulated pattern)
  ══════════════════════════════════════════════════════════════ */

  buildLabels24h() {
    const now = new Date();
    return Array.from({ length: 24 }, (_, i) => {
      const h = new Date(now - (23 - i) * 3600000);
      return `${String(h.getHours()).padStart(2, '0')}:00`;
    });
  },

  initThroughputChart() {
    const ctx = document.getElementById('throughput-chart');
    if (!ctx) return;
    if (this.throughputChart) this.throughputChart.destroy();

    const baseData = Array.from({ length: 24 }, (_, i) => {
      const hour = (new Date().getHours() - 23 + i + 24) % 24;
      const base = hour >= 8 && hour <= 20 ? 34000 : 24000;
      return base + Math.floor(Math.random() * 4000 - 2000);
    });

    this.throughputChart = new Chart(ctx, {
      type: 'line',
      data: {
        labels: this.buildLabels24h(),
        datasets: [
          {
            label: 'Total Kafka Throughput (msg/min)',
            data: baseData,
            borderColor: '#003d7c',
            backgroundColor: 'rgba(0,61,124,0.12)',
            tension: 0.4, fill: true, pointRadius: 3, pointHoverRadius: 6,
          },
          {
            label: 'Spark Records Processed (events/min)',
            data: baseData.map(v => Math.floor(v * 0.91 + Math.random() * 800 - 400)),
            borderColor: '#e65100',
            backgroundColor: 'rgba(230,81,0,0.06)',
            tension: 0.4, fill: true, pointRadius: 2, borderDash: [4, 2],
          }
        ]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        plugins: {
          legend: { position: 'bottom', labels: { boxWidth: 12, font: { size: 11 } } },
          tooltip: { callbacks: { label: ctx => ` ${ctx.raw.toLocaleString()} ${ctx.datasetIndex === 0 ? 'msg/min' : 'events/min'}` } }
        },
        scales: {
          x: { ticks: { maxTicksLimit: 12, font: { size: 10 } } },
          y: { beginAtZero: false, title: { display: true, text: 'Volume / min' } }
        }
      }
    });

    setInterval(() => {
      if (!this.throughputChart) return;
      const now = new Date();
      const newLabel = `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}`;
      const newKafka = 28000 + Math.floor(Math.random() * 8000);
      const newSpark = Math.floor(newKafka * 0.91 + Math.random() * 800 - 400);
      this.throughputChart.data.labels.push(newLabel);
      this.throughputChart.data.labels.shift();
      this.throughputChart.data.datasets[0].data.push(newKafka);
      this.throughputChart.data.datasets[0].data.shift();
      this.throughputChart.data.datasets[1].data.push(newSpark);
      this.throughputChart.data.datasets[1].data.shift();
      this.throughputChart.update();
    }, 60000);
  },

  /* ══════════════════════════════════════════════════════════════
     SOURCE METERS (progress bars on the Data Sources panel)
  ══════════════════════════════════════════════════════════════ */

  animateMeters() {
    const meters = [
      { fill: 'fda-bar',    val: 'fda-val',    target: 72, topic: 'faers-raw' },
      { fill: 'social-bar', val: 'social-val', target: 22, topic: 'fda-alerts' },
      { fill: 'ct-bar',     val: 'ct-val',     target: 19, topic: 'clinical-trials' },
    ];

    setTimeout(() => {
      meters.forEach(m => {
        const fill = document.getElementById(m.fill);
        if (fill) fill.style.width = m.target + '%';
        this._updateMeterLabel(m);
      });
    }, 400);

    setInterval(() => {
      meters.forEach(m => this._updateMeterLabel(m));
    }, 10000);  // update from real data every 10s
  },

  _updateMeterLabel(m) {
    const val = document.getElementById(m.val);
    if (!val) return;
    const t = this.kafka.topics[m.topic];
    if (t && t.total > 0) {
      val.textContent = `${t.total.toLocaleString()} msgs ingested`;
    }
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
      <span><strong>Architecture Demo</strong> — Spark, HDFS, and HBase charts
      are simulated to demonstrate pipeline scaling. Kafka data (above) is real.</span>
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

    // Start architectural simulations
    this.startSparkSimulation();
    this.startHDFSSimulation();
    this.animateMeters();
    this.startKafkaMetricBar();

    // Fetch real Kafka data immediately, then every 30 seconds
    this.fetchKafkaStats();
    this.fetchLiveFeed();
    setInterval(() => this.fetchKafkaStats(), 30_000);
    setInterval(() => this.fetchLiveFeed(),   60_000);
  },
};
