/* ============================================================
   distributed_storage.js — Big Data Pipeline Engine
   
   Simulates the actual behaviour of:
     • Apache Kafka   — topic partitions, offsets, consumer lag, throughput
     • Apache Spark   — micro-batch jobs, stage timing, task parallelism
     • HDFS           — block replication, write throughput, storage growth
     • Apache HBase   — real-time put/get stats
   
   All timers tick independently, creating realistic non-uniform load.
   ============================================================ */

const DistributedStorage = {

  /* ── Internal state (mimicking actual Kafka / Spark internals) ── */
  kafka: {
    topics: {
      'faers-raw':       { partitions: 12, offset: 8_420_301, lag: 41  },
      'ehr-stream':      { partitions: 8,  offset: 5_183_020, lag: 28  },
      'social-nlp':      { partitions: 6,  offset: 3_091_440, lag: 67  },
      'clinical-trials': { partitions: 4,  offset: 1_240_850, lag: 15  },
    },
    brokers: 3,
    replicationFactor: 2,
  },

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
  sparkStageChart: null,

  /* ──────────────────────────────────────────────────────────────
     KAFKA PARTITION SIMULATOR
     Increments offsets, fluctuates lag, broadcasts to UI
  ────────────────────────────────────────────────────────────── */
  startKafkaSimulation() {
    const topicKeys = Object.keys(this.kafka.topics);

    // Tick every 800ms — simulates Kafka commit loop
    setInterval(() => {
      topicKeys.forEach(topic => {
        const t = this.kafka.topics[topic];
        // Produce messages at different rates per topic
        const produced = topic === 'faers-raw'       ? Math.floor(Math.random() * 400 + 200)
                       : topic === 'ehr-stream'      ? Math.floor(Math.random() * 260 + 140)
                       : topic === 'social-nlp'      ? Math.floor(Math.random() * 180 + 80)
                       : Math.floor(Math.random() * 60 + 30);

        const consumed = Math.floor(produced * (0.92 + Math.random() * 0.12)); // 92–104% consumption
        t.offset += produced;
        t.lag = Math.max(0, t.lag + (produced - consumed));
      });

      // Update lag chart live
      this._updateKafkaLagChart();

      // Update partition metrics in pipeline health panel
      const allLag = topicKeys.reduce((s, k) => s + this.kafka.topics[k].lag, 0);
      const totalOffset = topicKeys.reduce((s, k) => s + this.kafka.topics[k].offset, 0);
      const kafkaEl = document.getElementById('kafka-metric');
      if (kafkaEl) {
        const msgsPerSec = topicKeys.reduce((s, k) => {
          const t = this.kafka.topics[k];
          return s + Math.floor(t.partitions * (Math.random() * 2800 + 1200));
        }, 0);
        kafkaEl.textContent = `${msgsPerSec.toLocaleString()} msg/s | Lag: ${allLag}`;
      }

    }, 800);

    // Every 5s update the offset counter for arch diagram nodes
    setInterval(() => {
      const nodes = [
        { id: 'fda-rate',    topic: 'faers-raw' },
        { id: 'ehr-rate',    topic: 'ehr-stream' },
        { id: 'social-rate', topic: 'social-nlp' },
        { id: 'ct-rate',     topic: 'clinical-trials' },
      ];
      nodes.forEach(n => {
        const el = document.getElementById(n.id);
        if (!el) return;
        const t = this.kafka.topics[n.topic];
        const rate = Math.floor(t.partitions * (Math.random() * 1200 + 600));
        el.textContent = `${rate.toLocaleString()} msg/min | offset: ${(t.offset / 1_000_000).toFixed(2)}M`;
      });
    }, 5000);
  },

  /* ──────────────────────────────────────────────────────────────
     SPARK MICRO-BATCH SIMULATOR
     Models Spark Streaming DStream micro-batch processing: 
     each 500ms triggers a new batch job with N tasks across executors
  ────────────────────────────────────────────────────────────── */
  startSparkSimulation() {
    // A new Spark micro-batch job fires every 500ms (the batch interval)
    setInterval(() => {
      const topicKeys = Object.keys(this.kafka.topics);
      const totalIncoming = topicKeys.reduce((s, k) => {
        const t = this.kafka.topics[k];
        return s + Math.floor(t.partitions * (Math.random() * 180 + 80));
      }, 0);

      this.spark.activeBatches = Math.floor(Math.random() * 3) + 1;
      this.spark.recordsPerBatch = totalIncoming;
      this.spark.stagesCompleted += 2; // schema map + PRR computation = 2 stages
      this.spark.tasksRunning = this.spark.executors * this.spark.coresPerExecutor;
      this.spark.totalTasksCompleted += this.spark.tasksRunning;

      // Update Spark metric in pipeline panel
      const sparkEl = document.getElementById('spark-metric');
      if (sparkEl) {
        const eventsPerSec = Math.floor(totalIncoming * (1000 / this.spark.batchDurationMs));
        sparkEl.textContent = `${eventsPerSec.toLocaleString()} events/s | ${this.spark.activeBatches} active batch(es)`;
      }

      // Update spark stage chart if visible
      this._updateSparkStageChart();

    }, this.spark.batchDurationMs);

    // Announce completed tasks in a running log
    setInterval(() => {
      const logEl = document.getElementById('spark-task-log');
      if (!logEl) return;
      const stages = ['Schema normalisation', 'MedDRA mapping', 'BioBERT UDF', 'PRR computation', 'LSTM prediction', 'HBase write'];
      const stage = stages[Math.floor(Math.random() * stages.length)];
      const tasks = this.spark.executors * this.spark.coresPerExecutor;
      const ms = Math.floor(Math.random() * 140 + 60);
      const entry = document.createElement('div');
      entry.className = 'spark-log-entry';
      entry.innerHTML = `<span class="spark-log-time">${new Date().toLocaleTimeString()}</span> Stage: <b>${stage}</b> — ${tasks} tasks / ${ms}ms`;
      logEl.prepend(entry);
      // Keep only last 8 log lines
      while (logEl.children.length > 8) logEl.removeChild(logEl.lastChild);
    }, 1200);
  },

  /* ──────────────────────────────────────────────────────────────
     HDFS + HBASE SIMULATOR
  ────────────────────────────────────────────────────────────── */
  startHDFSSimulation() {
    setInterval(() => {
      // HDFS grows as Spark writes Parquet blocks
      const newBlocks = Math.floor(Math.random() * 12 + 4);
      this.hdfs.totalBlocks += newBlocks;
      this.hdfs.replicatedBlocks += Math.floor(newBlocks * this.kafka.replicationFactor);
      this.hdfs.totalGB += Math.random() * 0.04; // ~40 MB per interval
      this.hdfs.writtenMBLast5min += Math.random() * 18 + 8;
      if (this.hdfs.writtenMBLast5min > 500) this.hdfs.writtenMBLast5min = 0; // rolling window reset

      const hdfsEl = document.getElementById('hdfs-metric');
      if (hdfsEl) {
        hdfsEl.textContent = `${this.hdfs.totalGB.toFixed(1)} TB | ${this.hdfs.totalBlocks.toLocaleString()} blocks`;
      }
    }, 3000);

    // HBase put/get ops
    setInterval(() => {
      this.hbase.putOpsPerSec = Math.floor(Math.random() * 12000 + 8000);
      this.hbase.getOpsPerSec = Math.floor(Math.random() * 6000 + 3000);
      this.hbase.rowsScanned += Math.floor(Math.random() * 5000 + 2000);
    }, 1000);
  },

  /* ──────────────────────────────────────────────────────────────
     KAFKA LAG CHART (live-updating bar chart)
  ────────────────────────────────────────────────────────────── */
  initKafkaLagChart() {
    const ctx = document.getElementById('kafka-lag-chart');
    if (!ctx) return;
    if (this.kafkaLagChart) this.kafkaLagChart.destroy();

    const topicKeys = Object.keys(this.kafka.topics);
    const labels = topicKeys.map(k => k.replace('-', '\n'));

    this.kafkaLagChart = new Chart(ctx, {
      type: 'bar',
      data: {
        labels: ['faers-raw', 'ehr-stream', 'social-nlp', 'clinical-trials'],
        datasets: [{
          label: 'Consumer Lag (msgs)',
          data: topicKeys.map(k => this.kafka.topics[k].lag),
          backgroundColor: ['#003d7c', '#00695c', '#f0a500', '#7b1fa2'],
          borderRadius: 8,
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: { duration: 400 },
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: ctx => ` ${ctx.raw.toLocaleString()} msgs behind`,
              afterLabel: ctx => {
                const topic = Object.keys(this.kafka.topics)[ctx.dataIndex];
                const t = this.kafka.topics[topic];
                return `Partitions: ${t.partitions} | Offset: ${(t.offset/1e6).toFixed(2)}M`;
              }
            }
          }
        },
        scales: {
          y: {
            beginAtZero: true,
            title: { display: true, text: 'Consumer Lag (messages)' }
          }
        }
      }
    });
  },

  _updateKafkaLagChart() {
    if (!this.kafkaLagChart) return;
    const topicKeys = Object.keys(this.kafka.topics);
    this.kafkaLagChart.data.datasets[0].data = topicKeys.map(k => this.kafka.topics[k].lag);
    this.kafkaLagChart.update('none'); // skip animation for live feel
  },

  /* ──────────────────────────────────────────────────────────────
     THROUGHPUT CHART (sliding window — last 24h of Kafka msgs)
  ────────────────────────────────────────────────────────────── */
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
      // Realistic diurnal load pattern — peaks at daytime hours
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
            tension: 0.4,
            fill: true,
            pointRadius: 3,
            pointHoverRadius: 6,
          },
          {
            label: 'Spark Records Processed (events/min)',
            data: baseData.map(v => Math.floor(v * 0.91 + Math.random() * 800 - 400)),
            borderColor: '#e65100',
            backgroundColor: 'rgba(230,81,0,0.06)',
            tension: 0.4,
            fill: true,
            pointRadius: 2,
            borderDash: [4, 2],
          }
        ]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        plugins: {
          legend: { position: 'bottom', labels: { boxWidth: 12, font: { size: 11 } } },
          tooltip: {
            callbacks: {
              label: ctx => ` ${ctx.raw.toLocaleString()} ${ctx.datasetIndex === 0 ? 'msg/min' : 'events/min'}`
            }
          }
        },
        scales: {
          x: { ticks: { maxTicksLimit: 12, font: { size: 10 } } },
          y: {
            beginAtZero: false,
            title: { display: true, text: 'Volume / min' }
          }
        }
      }
    });

    // Slide the chart window forward every 60s
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

  /* ──────────────────────────────────────────────────────────────
     SPARK STAGE TIMELINE CHART (bar chart per stage)
  ────────────────────────────────────────────────────────────── */
  _updateSparkStageChart() {
    // Called every batch; updates data without rebuilding the chart
  },

  /* ──────────────────────────────────────────────────────────────
     DASHBOARD SOURCE METERS (animated progress bars)
  ────────────────────────────────────────────────────────────── */
  animateMeters() {
    const meters = [
      { fill: 'fda-bar',    val: 'fda-val',    target: 72, baseRate: 14000, topic: 'faers-raw' },
      { fill: 'ehr-bar',    val: 'ehr-val',    target: 54, baseRate: 9840,  topic: 'ehr-stream' },
      { fill: 'social-bar', val: 'social-val', target: 38, baseRate: 6120,  topic: 'social-nlp' },
      { fill: 'ct-bar',     val: 'ct-val',     target: 19, baseRate: 2340,  topic: 'clinical-trials' },
    ];

    setTimeout(() => {
      meters.forEach(m => {
        const fill = document.getElementById(m.fill);
        const val  = document.getElementById(m.val);
        if (fill) fill.style.width = m.target + '%';
        if (val) {
          const topic = this.kafka.topics[m.topic];
          const rate = m.baseRate + Math.floor(Math.random() * 400 - 200);
          val.textContent = `${rate.toLocaleString()} msg/min`;
        }
      });
    }, 400);

    // Live-update the meter labels every 3s
    setInterval(() => {
      meters.forEach(m => {
        const val = document.getElementById(m.val);
        if (!val) return;
        const topic = this.kafka.topics[m.topic];
        const rate = m.baseRate + Math.floor(Math.random() * 600 - 300);
        val.textContent = `${rate.toLocaleString()} msg/min`;
      });
    }, 3000);
  },

  /* ──────────────────────────────────────────────────────────────
     PIPELINE HEALTH METRICS (Dashboard status panel)
  ────────────────────────────────────────────────────────────── */
  updatePipelineMetrics() {
    // Kafka
    setInterval(() => {
      const el = document.getElementById('kafka-metric');
      if (!el) return;
      const totalTopics = Object.keys(this.kafka.topics);
      const throughput = totalTopics.reduce((s, k) =>
        s + this.kafka.topics[k].partitions * (Math.floor(Math.random() * 3000 + 1000)), 0);
      const totalLag = totalTopics.reduce((s, k) => s + this.kafka.topics[k].lag, 0);
      el.textContent = `${(throughput / 1000).toFixed(1)}k msg/s · lag ${totalLag}`;
    }, 900);

    // Spark
    setInterval(() => {
      const el = document.getElementById('spark-metric');
      if (!el) return;
      const evtPerSec = Math.floor(this.spark.recordsPerBatch * 2 + Math.random() * 5000);
      el.textContent = `${evtPerSec.toLocaleString()} events/s · ${this.spark.activeBatches || 2} batches`;
    }, 900);

    // NLP — driven by ml_models.js BioBERT real throughput measurement.
    // LSTM — driven by real backend fetch in app.js updatePipelineMetrics().
    // Both are omitted here to avoid overwriting real values with fake ones.

    // HDFS
    setInterval(() => {
      const el = document.getElementById('hdfs-metric');
      if (!el) return;
      el.textContent = `${this.hdfs.totalGB.toFixed(1)} TB · ${this.hbase.putOpsPerSec.toLocaleString()} put/s`;
    }, 1500);
  },

  /* ──────────────────────────────────────────────────────────────
     BOOT
  ────────────────────────────────────────────────────────────── */
  boot() {
    this.startKafkaSimulation();
    this.startSparkSimulation();
    this.startHDFSSimulation();
    this.updatePipelineMetrics();
    this.animateMeters();

    // Inject disclaimer badge on Data Sources tab
    const container = document.querySelector('#data-sources .container.section-body');
    if (container && !document.getElementById('data-sources-demo-badge')) {
      const badge = document.createElement('div');
      badge.id = 'data-sources-demo-badge';
      badge.style.cssText = `
        display: flex;
        align-items: flex-start;
        gap: 8px;
        background: rgba(255, 193, 7, 0.15);
        border: 1px solid rgba(255, 193, 7, 0.4);
        border-radius: 8px;
        padding: 8px 14px;
        font-size: 0.78rem;
        color: #f0a500;
        font-weight: 600;
        margin: 0 0 1.5rem 0;
        line-height: 1.4;
      `;
      badge.innerHTML = `
        <span style="font-size:1rem; flex-shrink:0;">⚡</span>
        <span><strong>Architecture Demo</strong> — Kafka, Spark, HDFS, and HBase throughput charts and logs are simulated to demonstrate pipeline scaling and architecture layout. Only BioBERT NLP and LSTM models consume real clinical datasets.</span>
      `;
      container.insertBefore(badge, container.firstChild);
    }
  }
};
