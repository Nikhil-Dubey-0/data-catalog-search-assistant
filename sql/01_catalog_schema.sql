-- =============================================================================
-- Catalog database schema (SQLite).  Layers:
--   control  : etl_run, quarantine, change_log, search_log
--   raw      : raw_*  (append-only, one snapshot per run, values kept as received)
--   staging  : stg_*  (rebuilt every run; standardised + validated)
--   curated  : access_role, user_role, role_grant, catalog_table, catalog_column,
--              glossary_term
--   search   : search_doc (FTS5 index over curated metadata)
-- Every statement is idempotent (IF NOT EXISTS / OR IGNORE).
-- =============================================================================

-- ---------------------------------------------------------------- control ----
CREATE TABLE IF NOT EXISTS etl_run (
    run_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at       TEXT    NOT NULL,
    finished_at      TEXT,
    status           TEXT    NOT NULL CHECK (status IN ('running', 'success', 'skipped', 'failed')),
    fingerprint      TEXT,
    metadata_sources TEXT,
    stats_json       TEXT,
    error            TEXT
);

CREATE TABLE IF NOT EXISTS quarantine (
    quarantine_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id        INTEGER NOT NULL REFERENCES etl_run (run_id),
    entity        TEXT    NOT NULL,
    source_file   TEXT,
    source_row    INTEGER,
    record_key    TEXT,
    reason        TEXT    NOT NULL,
    raw_record    TEXT
);

CREATE TABLE IF NOT EXISTS change_log (
    change_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id         INTEGER NOT NULL REFERENCES etl_run (run_id),
    entity_type    TEXT    NOT NULL CHECK (entity_type IN ('table', 'column')),
    entity_key     TEXT    NOT NULL,
    table_key      TEXT    NOT NULL,
    change_type    TEXT    NOT NULL CHECK (change_type IN ('added', 'modified', 'removed', 'reactivated')),
    changed_fields TEXT
);
CREATE INDEX IF NOT EXISTS ix_change_log_run ON change_log (run_id);

CREATE TABLE IF NOT EXISTS search_log (
    query_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    asked_at       TEXT    NOT NULL,
    user_role      TEXT    NOT NULL,
    question       TEXT    NOT NULL,
    status         TEXT    NOT NULL,
    provider       TEXT    NOT NULL,
    ai_validation  TEXT,
    top_tables     TEXT,
    latency_ms     INTEGER
);

-- -------------------------------------------------------------------- raw ----
CREATE TABLE IF NOT EXISTS raw_technical_table (
    run_id       INTEGER NOT NULL REFERENCES etl_run (run_id),
    schema_name  TEXT    NOT NULL,
    table_name   TEXT    NOT NULL,
    row_count    INTEGER,
    harvested_at TEXT    NOT NULL,
    PRIMARY KEY (run_id, schema_name, table_name)
);

CREATE TABLE IF NOT EXISTS raw_technical_column (
    run_id         INTEGER NOT NULL REFERENCES etl_run (run_id),
    schema_name    TEXT    NOT NULL,
    table_name     TEXT    NOT NULL,
    column_name    TEXT    NOT NULL,
    ordinal        INTEGER NOT NULL,
    data_type      TEXT,
    is_nullable    INTEGER,
    is_primary_key INTEGER,
    null_fraction  REAL,
    distinct_count INTEGER,
    PRIMARY KEY (run_id, schema_name, table_name, column_name)
);

-- Steward CSV rows land here untouched (all TEXT) so bad input can be audited.
CREATE TABLE IF NOT EXISTS raw_business_table (
    run_id INTEGER NOT NULL REFERENCES etl_run (run_id),
    source_file TEXT NOT NULL, source_seq INTEGER NOT NULL, source_row INTEGER NOT NULL,
    schema_name TEXT, table_name TEXT, description TEXT, owner TEXT, domain TEXT, tags TEXT,
    access_role TEXT, refresh_frequency TEXT, certified TEXT,
    PRIMARY KEY (run_id, source_seq)
);

CREATE TABLE IF NOT EXISTS raw_business_column (
    run_id INTEGER NOT NULL REFERENCES etl_run (run_id),
    source_file TEXT NOT NULL, source_seq INTEGER NOT NULL, source_row INTEGER NOT NULL,
    schema_name TEXT, table_name TEXT, column_name TEXT, description TEXT, is_pii TEXT,
    access_role TEXT,
    PRIMARY KEY (run_id, source_seq)
);

CREATE TABLE IF NOT EXISTS raw_glossary (
    run_id INTEGER NOT NULL REFERENCES etl_run (run_id),
    source_file TEXT NOT NULL, source_seq INTEGER NOT NULL, source_row INTEGER NOT NULL,
    term TEXT, definition TEXT, synonyms TEXT, related_tables TEXT,
    PRIMARY KEY (run_id, source_seq)
);

CREATE TABLE IF NOT EXISTS raw_access_role (
    run_id INTEGER NOT NULL REFERENCES etl_run (run_id),
    source_file TEXT NOT NULL, source_seq INTEGER NOT NULL, source_row INTEGER NOT NULL,
    access_role TEXT, description TEXT, sensitivity_rank TEXT,
    PRIMARY KEY (run_id, source_seq)
);

CREATE TABLE IF NOT EXISTS raw_user_role (
    run_id INTEGER NOT NULL REFERENCES etl_run (run_id),
    source_file TEXT NOT NULL, source_seq INTEGER NOT NULL, source_row INTEGER NOT NULL,
    user_role TEXT, description TEXT, grants TEXT,
    PRIMARY KEY (run_id, source_seq)
);

-- ---------------------------------------------------------------- staging ----
CREATE TABLE IF NOT EXISTS stg_business_table (
    source_file TEXT, source_seq INTEGER PRIMARY KEY, source_row INTEGER,
    schema_name TEXT, table_name TEXT, description TEXT, owner TEXT, domain TEXT, tags TEXT,
    access_role TEXT, refresh_frequency TEXT, certified_raw TEXT, certified INTEGER,
    reject_reason TEXT
);

CREATE TABLE IF NOT EXISTS stg_business_column (
    source_file TEXT, source_seq INTEGER PRIMARY KEY, source_row INTEGER,
    schema_name TEXT, table_name TEXT, column_name TEXT, description TEXT,
    is_pii_raw TEXT, is_pii INTEGER, access_role TEXT,
    reject_reason TEXT
);

CREATE TABLE IF NOT EXISTS stg_glossary (
    source_file TEXT, source_seq INTEGER PRIMARY KEY, source_row INTEGER,
    term TEXT, definition TEXT, synonyms TEXT, related_tables TEXT,
    reject_reason TEXT
);

-- ---------------------------------------------------------------- curated ----
CREATE TABLE IF NOT EXISTS access_role (
    access_role      TEXT PRIMARY KEY,
    description      TEXT,
    sensitivity_rank INTEGER NOT NULL DEFAULT 0
);
-- The fail-closed default must always exist, even before any CSV is loaded.
INSERT OR IGNORE INTO access_role (access_role, description, sensitivity_rank)
VALUES ('restricted', 'Fail-closed default for unclassified data (admin only)', 9);

CREATE TABLE IF NOT EXISTS user_role (
    user_role   TEXT PRIMARY KEY,
    description TEXT
);

CREATE TABLE IF NOT EXISTS role_grant (
    user_role   TEXT NOT NULL REFERENCES user_role (user_role),
    access_role TEXT NOT NULL REFERENCES access_role (access_role),
    PRIMARY KEY (user_role, access_role)
);

CREATE TABLE IF NOT EXISTS catalog_table (
    table_key         TEXT PRIMARY KEY,              -- schema.table
    schema_name       TEXT    NOT NULL,
    table_name        TEXT    NOT NULL,
    description       TEXT,
    owner             TEXT,
    domain            TEXT,
    tags              TEXT,                          -- ';'-separated, lower case
    access_role       TEXT    NOT NULL REFERENCES access_role (access_role),
    access_source     TEXT    NOT NULL,              -- steward | default_restricted
    refresh_frequency TEXT,
    certified         INTEGER NOT NULL DEFAULT 0,
    row_count         INTEGER,
    column_count      INTEGER,
    is_documented     INTEGER NOT NULL,
    is_active         INTEGER NOT NULL DEFAULT 1,
    first_seen_run    INTEGER NOT NULL,
    last_changed_run  INTEGER NOT NULL,
    last_seen_run     INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS catalog_column (
    column_key       TEXT PRIMARY KEY,               -- schema.table.column
    table_key        TEXT    NOT NULL REFERENCES catalog_table (table_key),
    column_name      TEXT    NOT NULL,
    ordinal          INTEGER NOT NULL,
    data_type        TEXT,
    is_nullable      INTEGER,
    is_primary_key   INTEGER,
    description      TEXT,
    is_pii           INTEGER NOT NULL DEFAULT 0,
    access_role      TEXT REFERENCES access_role (access_role),  -- NULL = inherit table
    access_source    TEXT    NOT NULL,               -- steward | pii_flag | auto_pii_name_rule | inherited
    null_fraction    REAL,
    distinct_count   INTEGER,
    is_documented    INTEGER NOT NULL,
    is_active        INTEGER NOT NULL DEFAULT 1,
    first_seen_run   INTEGER NOT NULL,
    last_changed_run INTEGER NOT NULL,
    last_seen_run    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_catalog_column_table ON catalog_column (table_key);

CREATE TABLE IF NOT EXISTS glossary_term (
    term           TEXT PRIMARY KEY,
    definition     TEXT NOT NULL,
    synonyms       TEXT,                             -- ';'-separated
    related_tables TEXT,                             -- ';'-separated table keys
    last_run       INTEGER NOT NULL
);

-- ----------------------------------------------------------------- search ----
-- One document per table and per column. Access roles are stored UNINDEXED so the
-- role filter is applied inside the same SQL query that ranks the documents.
CREATE VIRTUAL TABLE IF NOT EXISTS search_doc USING fts5 (
    doc_type UNINDEXED,
    table_key UNINDEXED,
    column_name UNINDEXED,
    table_access_role UNINDEXED,
    column_access_role UNINDEXED,
    name,
    description,
    context,
    tokenize = 'porter unicode61'
);
