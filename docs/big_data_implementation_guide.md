# Big Data Implementation Guide for PharmaWatch

Currently, the PharmaWatch dashboard uses `src/distributed_storage.js` to **simulate** a big data pipeline. To turn this into a real production-grade big data pipeline, we need to replace the simulated data with actual distributed systems: **Apache Kafka**, **Apache Spark Streaming**, and **Hadoop (HDFS & HBase)**.

Implementing this requires setting up independent clusters, writing real streaming jobs, and connecting them to your Python backend. Here is the step-by-step proper guide.

---

## 🏗️ Step 1: High-Level Architecture

The actual data flow will look like this:

1. **Ingestion (Kafka):** Python scripts pull raw data from openFDA, Twitter API, and EHR streams, pushing the messages into specific Kafka topics (`faers-raw`, `ehr-stream`).
2. **Processing (Spark Streaming):** An Apache Spark application subscribes to these Kafka topics, cleans the text, runs BioBERT inference (NER), calculates PRR (Proportional Reporting Ratios), and aggregates the data in 500ms micro-batches.
3. **Storage (HDFS / HBase):** Spark writes the aggregated PRR metrics and processed events to Apache HBase. Large raw data files (like full JSON responses) are dumped into HDFS in Parquet format.
4. **API Layer (Flask/FastAPI):** Your backend connects to HBase to fetch the live metrics.
5. **Frontend (Dashboard):** `distributed_storage.js` is rewritten to query the backend every second to display real metrics.

---

## 🐳 Step 2: Provision Local Infrastructure (Docker Compose)

You should run the big data tools using **Docker Compose** rather than installing them manually on Windows.

1. Create a `docker-compose.yml` in the root of your project:
```yaml
version: '3'
services:
  zookeeper:
    image: confluentinc/cp-zookeeper:latest
    environment:
      ZOOKEEPER_CLIENT_PORT: 2181
  
  kafka:
    image: confluentinc/cp-kafka:latest
    depends_on:
      - zookeeper
    ports:
      - "9092:9092"
    environment:
      KAFKA_ZOOKEEPER_CONNECT: zookeeper:2181
      KAFKA_ADVERTISED_LISTENERS: PLAINTEXT://localhost:9092
      KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR: 1

  hbase:
    image: harisekhon/hbase
    ports:
      - "2181:2181"
      - "16010:16010" # Web UI
```
2. Start the cluster: `docker-compose up -d`

---

## 🔌 Step 3: Implement Kafka Ingestion

Instead of random numbers, we need a script that actually streams data to Kafka.

1. Install kafka-python in your backend environment: `pip install kafka-python`
2. Create `backend/producers.py`:
```python
from kafka import KafkaProducer
import json
import time
import requests

producer = KafkaProducer(
    bootstrap_servers=['localhost:9092'],
    value_serializer=lambda v: json.dumps(v).encode('utf-8')
)

def stream_openfda():
    # Fetch real live data and push to Kafka
    while True:
        data = requests.get('https://api.fda.gov/drug/event.json?limit=10').json()
        for event in data['results']:
            producer.send('faers-raw', event)
        time.sleep(1) # simulate streaming
```

---

## ⚡ Step 4: Implement Spark Streaming

You need to build a PySpark streaming application that reads from Kafka, processes with BioBERT, and writes to HBase/HDFS.

1. Install PySpark: `pip install pyspark`
2. Create `backend/spark_job.py`:
```python
from pyspark.sql import SparkSession
from pyspark.sql.functions import *

spark = SparkSession.builder \
    .appName("PharmaWatch-Streaming") \
    .config("spark.jars.packages", "org.apache.spark:spark-sql-kafka-0-10_2.12:3.3.0") \
    .getOrCreate()

# Read from Kafka
df = spark.readStream \
    .format("kafka") \
    .option("kafka.bootstrap.servers", "localhost:9092") \
    .option("subscribe", "faers-raw") \
    .load()

# Deserialize JSON
parsed_df = df.selectExpr("CAST(value AS STRING)")

# [Add BioBERT Mapping and PRR Math Here]

# Write out to HBase (pseudo-code)
query = parsed_df.writeStream \
    .foreachBatch(lambda batch_df, batch_id: batch_df.write.format("hbase").save()) \
    .start()
    
query.awaitTermination()
```

---

## 🔄 Step 5: Connect Flask Backend to Big Data

Modify `backend/app.py` so the Python server actually fetches the real stats from Kafka/HBase to pass to the frontend:

```python
from flask import jsonify
from pykafka import KafkaClient

@app.route('/api/metrics')
def get_metrics():
    # Example: Getting real Kafka topic offset
    client = KafkaClient(hosts="127.0.0.1:9092")
    topic = client.topics[b'faers-raw']
    lag = len(topic.earliest_available_offsets()) # Simplified
    
    return jsonify({
        "kafka_lag": lag,
        "spark_completed_batches": 12051,
        "hdfs_blocks": 5231
    })
```

---

## 🌐 Step 6: Rewrite the Frontend (`distributed_storage.js`)

Right now, `src/distributed_storage.js` has functions like `setInterval` that generate `Math.random()` numbers. 

You must replace them with active Fetch requests to your Flask backend:

```javascript
/* OLD JS:
const produced = Math.random() * 400 + 200;
this.kafka.topics['faers-raw'].offset += produced;
*/

/* NEW JS: */
async function fetchRealBigDataMetrics() {
    const res = await fetch('http://localhost:5000/api/metrics');
    const data = await res.json();
    
    // Update dashboard UI with REAL numbers
    document.getElementById('kafka-metric').textContent = `Lag: ${data.kafka_lag}`;
}

// Call the API every 2 seconds
setInterval(fetchRealBigDataMetrics, 2000);
```

## Summary Checklist

- [ ] Install Docker and spin up Zookeeper, Kafka, and HBase.
- [ ] Write Python Producers (`producers.py`) to scrape FDA/Twitter into Kafka.
- [ ] Run PySpark streaming (`spark_job.py`) to process Kafka topics and calculate metrics.
- [ ] Update Flask `app.py` to expose `/api/metrics` reading from Kafka Admin and HBase.
- [ ] Strip out all `Math.random()` calls in `src/distributed_storage.js` and replace with real API Fetch calls.
