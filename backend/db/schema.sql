-- ─────────────────────────────────────────────────────────────────────────────
-- PharmaWatch Production MySQL 8.0 Schema
-- Engine: InnoDB | Character Set: utf8mb4 | Collation: utf8mb4_unicode_ci
-- ─────────────────────────────────────────────────────────────────────────────

-- 1. Raw Drug Adverse Event Occurrences
CREATE TABLE IF NOT EXISTS drug_events (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    drug VARCHAR(191) NOT NULL,
    event VARCHAR(191) NOT NULL,
    source VARCHAR(64) NOT NULL DEFAULT 'manual',
    confidence DOUBLE NOT NULL DEFAULT 1.0,
    raw_hash CHAR(32) DEFAULT NULL,
    raw_text MEDIUMTEXT DEFAULT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_drug_events_drug (drug),
    INDEX idx_drug_events_event (event),
    INDEX idx_drug_events_source (source),
    INDEX idx_drug_events_created (created_at),
    UNIQUE KEY uq_drug_event_source_hash (drug, event, source, raw_hash)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 2. Aggregated Co-occurrence Matrix (High-speed disproportionality)
CREATE TABLE IF NOT EXISTS drug_events_agg (
    drug VARCHAR(191) NOT NULL,
    event VARCHAR(191) NOT NULL,
    report_count INT NOT NULL DEFAULT 0,
    source VARCHAR(64) NOT NULL DEFAULT 'faers',
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (drug, event),
    INDEX idx_agg_event (event),
    INDEX idx_agg_count (report_count)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 3. Social Media Scraped Posts (Reddit Mining)
CREATE TABLE IF NOT EXISTS reddit_posts (
    id VARCHAR(32) PRIMARY KEY,
    subreddit VARCHAR(64) NOT NULL,
    title TEXT NOT NULL,
    body LONGTEXT DEFAULT NULL,
    score INT NOT NULL DEFAULT 0,
    processed TINYINT(1) NOT NULL DEFAULT 0,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_reddit_processed (processed),
    INDEX idx_reddit_subreddit (subreddit)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 4. FDA Black Boxed Warning Violations
CREATE TABLE IF NOT EXISTS boxed_warning_violations (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    drug VARCHAR(191) NOT NULL,
    adverse_event VARCHAR(191) NOT NULL,
    faers_count_post_warning INT NOT NULL DEFAULT 0,
    faers_count_pre_warning INT NOT NULL DEFAULT 0,
    percentage_of_total DOUBLE NOT NULL DEFAULT 0.0,
    warning_effective_date VARCHAR(32) DEFAULT NULL,
    warning_text_snippet TEXT DEFAULT NULL,
    detection_method VARCHAR(64) NOT NULL DEFAULT 'post_warning_faers',
    queried_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_viol_drug_event (drug, adverse_event),
    INDEX idx_viol_drug (drug),
    INDEX idx_viol_event (adverse_event),
    INDEX idx_viol_queried (queried_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 5. Disproportionality Signals Cache (PRR, ROR, Chi-squared, IC)
CREATE TABLE IF NOT EXISTS signals_cache (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    drug VARCHAR(191) NOT NULL,
    event VARCHAR(191) NOT NULL,
    a INT NOT NULL DEFAULT 0,
    b INT NOT NULL DEFAULT 0,
    c INT NOT NULL DEFAULT 0,
    d INT NOT NULL DEFAULT 0,
    prr DOUBLE DEFAULT NULL,
    prr_ci_low DOUBLE DEFAULT NULL,
    prr_ci_high DOUBLE DEFAULT NULL,
    ror DOUBLE DEFAULT NULL,
    ror_ci_low DOUBLE DEFAULT NULL,
    ror_ci_high DOUBLE DEFAULT NULL,
    chi2 DOUBLE DEFAULT NULL,
    bcpnn_ic DOUBLE DEFAULT NULL,
    ic025 DOUBLE DEFAULT NULL,
    n_reports INT NOT NULL DEFAULT 0,
    signal_strength VARCHAR(32) DEFAULT NULL,
    source VARCHAR(64) NOT NULL DEFAULT 'faers',
    computed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_signals_drug_event (drug, event),
    INDEX idx_signals_drug (drug),
    INDEX idx_signals_event (event),
    INDEX idx_signals_prr (prr),
    INDEX idx_signals_computed (computed_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 6. Temporal Analysis Payload Cache
CREATE TABLE IF NOT EXISTS temporal_cache (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    drug VARCHAR(191) NOT NULL,
    event VARCHAR(191) NOT NULL,
    data_type VARCHAR(64) NOT NULL,
    payload LONGTEXT NOT NULL,
    cached_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_temporal_cache (drug, event, data_type),
    INDEX idx_temporal_cached (cached_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 7. Demographic Stratification Cache
CREATE TABLE IF NOT EXISTS demographic_cache (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    drug VARCHAR(191) NOT NULL,
    event VARCHAR(191) NOT NULL,
    strata_type VARCHAR(64) NOT NULL,
    payload LONGTEXT NOT NULL,
    cached_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_demo_cache (drug, event, strata_type),
    INDEX idx_demo_cached (cached_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 8. GNN Drug-Drug Interaction Predictions Cache
CREATE TABLE IF NOT EXISTS interaction_predictions (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    drug_a VARCHAR(191) NOT NULL,
    drug_b VARCHAR(191) NOT NULL,
    smiles_a TEXT DEFAULT NULL,
    smiles_b TEXT DEFAULT NULL,
    cid_a VARCHAR(32) DEFAULT NULL,
    cid_b VARCHAR(32) DEFAULT NULL,
    is_harmful TINYINT(1) NOT NULL DEFAULT 0,
    confidence DOUBLE NOT NULL DEFAULT 0.0,
    top_side_effect VARCHAR(191) DEFAULT NULL,
    all_side_effects LONGTEXT DEFAULT NULL,
    source VARCHAR(64) NOT NULL DEFAULT 'gnn',
    queried_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_interaction_pair (drug_a, drug_b),
    INDEX idx_pred_drug_a (drug_a),
    INDEX idx_pred_drug_b (drug_b),
    INDEX idx_pred_queried (queried_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 9. openFDA HTTP Response Cache (7-Day TTL)
CREATE TABLE IF NOT EXISTS fda_cache (
    url_hash CHAR(64) PRIMARY KEY,
    url TEXT NOT NULL,
    response_json LONGTEXT NOT NULL,
    cached_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_fda_cached (cached_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 10. Kafka PubMed Literature Stream
CREATE TABLE IF NOT EXISTS kafka_pubmed (
    pmid VARCHAR(32) PRIMARY KEY,
    title TEXT DEFAULT NULL,
    journal VARCHAR(255) DEFAULT NULL,
    pubdate VARCHAR(64) DEFAULT NULL,
    query TEXT DEFAULT NULL,
    ingested_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_pubmed_ingested (ingested_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 11. Kafka ClinicalTrials Stream
CREATE TABLE IF NOT EXISTS kafka_trials (
    nct_id VARCHAR(32) PRIMARY KEY,
    drug VARCHAR(191) DEFAULT NULL,
    title TEXT DEFAULT NULL,
    phase VARCHAR(64) DEFAULT NULL,
    status VARCHAR(64) DEFAULT NULL,
    ingested_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_trials_drug (drug),
    INDEX idx_trials_ingested (ingested_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 12. Kafka FDA MedWatch Safety Alerts Stream
CREATE TABLE IF NOT EXISTS kafka_alerts (
    alert_key VARCHAR(255) PRIMARY KEY,
    title TEXT DEFAULT NULL,
    link TEXT DEFAULT NULL,
    summary TEXT DEFAULT NULL,
    published VARCHAR(64) DEFAULT NULL,
    feed_url TEXT DEFAULT NULL,
    ingested_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_alerts_ingested (ingested_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 13. Kafka Pipeline Stats (Consumer Lag & Message Counters)
CREATE TABLE IF NOT EXISTS kafka_pipeline_stats (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    topic VARCHAR(64) NOT NULL,
    messages_in INT NOT NULL DEFAULT 1,
    last_offset VARCHAR(64) DEFAULT NULL,
    recorded_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_pipe_topic_recorded (topic, recorded_at),
    INDEX idx_pipe_recorded (recorded_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 14. Global FAERS Adverse Event Background Counts (Epidemiological Reference)
CREATE TABLE IF NOT EXISTS faers_background_events (
    event VARCHAR(191) PRIMARY KEY,
    global_count BIGINT NOT NULL DEFAULT 0,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
