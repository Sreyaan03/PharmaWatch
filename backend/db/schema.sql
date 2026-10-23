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
    data_version VARCHAR(160) DEFAULT NULL,
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

-- (9. The openFDA HTTP response cache (fda_cache) was removed in Phase 11: HTTP caching is handled
--  by the clients.http layer, not a MySQL table. The table is dropped by db.migrate.)

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
    partition_id INT NOT NULL DEFAULT 0,
    messages_in INT NOT NULL DEFAULT 1,
    last_offset VARCHAR(64) DEFAULT NULL,
    recorded_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_pipe_topic_recorded (topic, recorded_at),
    INDEX idx_pipe_recorded (recorded_at),
    UNIQUE KEY uq_pipe_message (topic, partition_id, last_offset)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Durable background jobs (uploads on disk until the job ends; see core/jobs.py)
CREATE TABLE IF NOT EXISTS jobs (
    id CHAR(36) NOT NULL PRIMARY KEY,
    kind VARCHAR(48) NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'queued',
    input_ref VARCHAR(255) DEFAULT NULL,
    meta_json MEDIUMTEXT DEFAULT NULL,
    progress VARCHAR(255) DEFAULT NULL,
    result_json LONGTEXT DEFAULT NULL,
    error TEXT DEFAULT NULL,
    created_by VARCHAR(64) DEFAULT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    expires_at DATETIME NOT NULL,
    lease_owner VARCHAR(64) DEFAULT NULL,
    lease_expires DATETIME DEFAULT NULL,
    attempts INT NOT NULL DEFAULT 0,
    INDEX idx_jobs_status (status),
    INDEX idx_jobs_expires (expires_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- FAERS quarters announced on the `faers-release` topic (written by the Kafka consumer)
CREATE TABLE IF NOT EXISTS faers_releases (
    quarter VARCHAR(8) NOT NULL PRIMARY KEY,
    status VARCHAR(16) NOT NULL DEFAULT 'available',
    job_id CHAR(36) DEFAULT NULL,
    detected_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- External evidence (alerts, papers, trials) linked to a drug ingredient and, when the text names
-- one, a MedDRA Preferred Term. pt is '' when no term was found. External evidence, not statistics.
CREATE TABLE IF NOT EXISTS evidence_links (
    rxcui VARCHAR(16) NOT NULL,
    ingredient VARCHAR(191) NOT NULL,
    pt VARCHAR(191) NOT NULL DEFAULT '',
    kind VARCHAR(16) NOT NULL,
    ref_id VARCHAR(191) NOT NULL,
    title TEXT DEFAULT NULL,
    url TEXT DEFAULT NULL,
    published VARCHAR(64) DEFAULT NULL,
    matched_text VARCHAR(191) DEFAULT NULL,
    linked_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_evidence (rxcui, pt, kind, ref_id),
    INDEX idx_evidence_rxcui (rxcui),
    INDEX idx_evidence_pt (pt)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- Latest stored FDA label (safety sections) per watched drug. The label watcher diffs a new
-- fetch against this row and publishes label-change; sections_json is {section: text}.
CREATE TABLE IF NOT EXISTS label_snapshots (
    rxcui VARCHAR(16) NOT NULL PRIMARY KEY,
    generic_name VARCHAR(191) DEFAULT NULL,
    set_id VARCHAR(64) DEFAULT NULL,
    version VARCHAR(16) DEFAULT NULL,
    effective_time VARCHAR(16) DEFAULT NULL,
    sections_json MEDIUMTEXT DEFAULT NULL,
    fetched_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_label_effective (effective_time)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Drugs a reviewer is watching (shared until accounts arrive in Phase 11).
CREATE TABLE IF NOT EXISTS surveillance_watchlist (
    rxcui VARCHAR(16) NOT NULL PRIMARY KEY,
    ingredient VARCHAR(191) NOT NULL,
    added_by VARCHAR(64) DEFAULT NULL,
    added_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Reviewable alerts. kind: new_signal | not_in_label | label_change. A disproportionality signal
-- is a reporting pattern, not proof of causation; these are possible signals for review.
-- status: new | reviewed | dismissed | escalated. detail_json carries the evidence (metrics,
-- label section and sentence) shown in the feed. One alert per (kind, rxcui, pt, quarter).
CREATE TABLE IF NOT EXISTS surveillance_alerts (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    kind VARCHAR(16) NOT NULL,
    rxcui VARCHAR(16) NOT NULL,
    ingredient VARCHAR(191) NOT NULL,
    pt VARCHAR(191) NOT NULL DEFAULT '',
    quarter VARCHAR(8) NOT NULL DEFAULT '',
    ic025 DOUBLE DEFAULT NULL,
    cases INT DEFAULT NULL,
    detail_json MEDIUMTEXT DEFAULT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'new',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    reviewed_by VARCHAR(64) DEFAULT NULL,
    reviewed_at DATETIME DEFAULT NULL,
    UNIQUE KEY uq_alert (kind, rxcui, pt, quarter),
    INDEX idx_alert_status (status),
    INDEX idx_alert_kind (kind),
    INDEX idx_alert_rxcui (rxcui),
    INDEX idx_alert_rank (ic025)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Reviewer feedback on a result, for use as pilot validation data. verdict: correct | incorrect |
-- unsure. subject_json identifies what the feedback is about (e.g. drug, event, component) and
-- carries the result's data/model versions so a judgement is tied to the version it referred to.
CREATE TABLE IF NOT EXISTS feedback (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    user_id BIGINT DEFAULT NULL,
    user_email VARCHAR(191) DEFAULT NULL,
    component VARCHAR(64) NOT NULL,
    subject_json TEXT DEFAULT NULL,
    verdict VARCHAR(16) NOT NULL,
    comment TEXT DEFAULT NULL,
    data_version VARCHAR(160) DEFAULT NULL,
    model_version VARCHAR(64) DEFAULT NULL,
    INDEX idx_feedback_at (at),
    INDEX idx_feedback_component (component),
    INDEX idx_feedback_verdict (verdict)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Append-only audit trail. One row per analysis, upload, confirmation, export, login and admin
-- action, with the user, the input identities/parameters (never document content or free text),
-- and the data and model versions the result came from. The application DB user should hold INSERT
-- and SELECT only on this table; the code writes no UPDATE or DELETE here.
CREATE TABLE IF NOT EXISTS audit_log (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    user_id BIGINT DEFAULT NULL,
    user_email VARCHAR(191) DEFAULT NULL,
    action VARCHAR(64) NOT NULL,
    route VARCHAR(191) NOT NULL,
    method VARCHAR(8) NOT NULL,
    inputs_json TEXT DEFAULT NULL,
    data_version VARCHAR(160) DEFAULT NULL,
    model_version VARCHAR(64) DEFAULT NULL,
    status INT DEFAULT NULL,
    request_id CHAR(32) DEFAULT NULL,
    INDEX idx_audit_at (at),
    INDEX idx_audit_user (user_id),
    INDEX idx_audit_action (action)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Accounts for the college pilot. Roles: viewer (read), analyst (run analyses, upload, feedback),
-- admin (users, training, data refresh, exports). No self-registration; created via core.manage.
-- Passwords are Werkzeug password hashes, never stored or logged in clear text.
CREATE TABLE IF NOT EXISTS users (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    email VARCHAR(191) NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    role VARCHAR(16) NOT NULL DEFAULT 'viewer',
    organisation VARCHAR(191) DEFAULT NULL,
    active TINYINT(1) NOT NULL DEFAULT 1,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_login DATETIME DEFAULT NULL,
    UNIQUE KEY uq_users_email (email),
    INDEX idx_users_role (role)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Isolated replay of history. An admin replays the loaded quarters through the SAME alert
-- detector, quarter by quarter, writing only here (never to surveillance_alerts), keyed by a
-- run id. These are the alerts the system WOULD have raised at the time; a replay changes no
-- live data. The quarters list is stored so a quarter's "previous" is known during stepping.
CREATE TABLE IF NOT EXISTS replay_runs (
    run_id CHAR(32) NOT NULL PRIMARY KEY,
    from_quarter VARCHAR(8) NOT NULL,
    to_quarter VARCHAR(8) NOT NULL,
    quarters_json TEXT NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'running',   -- running | done
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Alerts produced by a replay. One row per (run, quarter, kind, rxcui, pt), so re-stepping a
-- quarter changes nothing. detail_json carries the same evidence as a live alert.
CREATE TABLE IF NOT EXISTS replay_alerts (
    run_id CHAR(32) NOT NULL,
    quarter VARCHAR(8) NOT NULL,
    kind VARCHAR(16) NOT NULL,
    rxcui VARCHAR(16) NOT NULL,
    ingredient VARCHAR(191) NOT NULL,
    pt VARCHAR(191) NOT NULL DEFAULT '',
    ic025 DOUBLE DEFAULT NULL,
    cases INT DEFAULT NULL,
    detail_json MEDIUMTEXT DEFAULT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (run_id, quarter, kind, rxcui, pt),
    INDEX idx_replay_run_q (run_id, quarter)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
