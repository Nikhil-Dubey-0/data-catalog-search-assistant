-- =============================================================================
-- Step 1: raw steward CSV rows (this run) -> standardised staging rows.
-- Bad rows are not dropped silently: each gets a reject_reason and is copied to
-- quarantine with the original raw record as JSON.
-- Parameter: :run_id
-- =============================================================================

-- ---------------------------------------------------------- access roles ----
-- Upsert (never delete): catalog rows reference access_role via foreign keys.
INSERT INTO access_role (access_role, description, sensitivity_rank)
SELECT LOWER(TRIM(access_role)), NULLIF(TRIM(description), ''), CAST(COALESCE(NULLIF(TRIM(sensitivity_rank), ''), '0') AS INTEGER)
FROM raw_access_role
WHERE run_id = :run_id AND TRIM(COALESCE(access_role, '')) <> ''
ORDER BY source_seq
ON CONFLICT (access_role) DO UPDATE SET
    description      = excluded.description,
    sensitivity_rank = excluded.sensitivity_rank;

-- ------------------------------------------------------------ user roles ----
-- Explode 'public;internal' grant lists with a recursive CTE.
DROP TABLE IF EXISTS temp.tmp_grant;
CREATE TEMP TABLE tmp_grant AS
WITH RECURSIVE latest AS (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY LOWER(TRIM(user_role)) ORDER BY source_seq DESC) AS rn
    FROM raw_user_role
    WHERE run_id = :run_id AND TRIM(COALESCE(user_role, '')) <> ''
),
split (user_role, source_file, source_row, item, rest) AS (
    SELECT LOWER(TRIM(user_role)), source_file, source_row, '',
           LOWER(REPLACE(COALESCE(grants, ''), ' ', '')) || ';'
    FROM latest WHERE rn = 1
    UNION ALL
    SELECT user_role, source_file, source_row,
           SUBSTR(rest, 1, INSTR(rest, ';') - 1),
           SUBSTR(rest, INSTR(rest, ';') + 1)
    FROM split WHERE rest <> ''
)
SELECT DISTINCT user_role, source_file, source_row, item AS access_role
FROM split WHERE item <> '';

INSERT INTO quarantine (run_id, entity, source_file, source_row, record_key, reason)
SELECT :run_id, 'user_role_grant', source_file, source_row, user_role || ' -> ' || access_role,
       'unknown access_role ''' || access_role || ''' in grants'
FROM tmp_grant
WHERE access_role NOT IN (SELECT access_role FROM access_role);

-- Small reference data: full refresh inside the run transaction.
DELETE FROM role_grant;
DELETE FROM user_role;
INSERT INTO user_role (user_role, description)
SELECT LOWER(TRIM(user_role)), NULLIF(TRIM(description), '')
FROM (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY LOWER(TRIM(user_role)) ORDER BY source_seq DESC) AS rn
    FROM raw_user_role WHERE run_id = :run_id AND TRIM(COALESCE(user_role, '')) <> ''
) WHERE rn = 1;
INSERT INTO role_grant (user_role, access_role)
SELECT DISTINCT user_role, access_role FROM tmp_grant
WHERE access_role IN (SELECT access_role FROM access_role);

-- -------------------------------------------------- business table rows ----
DELETE FROM stg_business_table;
INSERT INTO stg_business_table (
    source_file, source_seq, source_row, schema_name, table_name, description, owner, domain,
    tags, access_role, refresh_frequency, certified_raw, certified)
SELECT
    source_file, source_seq, source_row,
    LOWER(TRIM(schema_name)),
    LOWER(TRIM(table_name)),
    NULLIF(TRIM(description), ''),
    NULLIF(LOWER(TRIM(owner)), ''),
    NULLIF(LOWER(TRIM(domain)), ''),
    NULLIF(LOWER(REPLACE(REPLACE(TRIM(tags), ', ', ';'), ',', ';')), ''),
    NULLIF(LOWER(TRIM(access_role)), ''),
    NULLIF(LOWER(TRIM(refresh_frequency)), ''),
    certified,
    CASE
        WHEN TRIM(COALESCE(certified, '')) = '' THEN 0
        WHEN UPPER(TRIM(certified)) IN ('Y', 'YES', 'TRUE', '1') THEN 1
        WHEN UPPER(TRIM(certified)) IN ('N', 'NO', 'FALSE', '0') THEN 0
    END                                   -- NULL = unparseable flag
FROM raw_business_table
WHERE run_id = :run_id;

-- Validation rules, first failing rule wins.
UPDATE stg_business_table SET reject_reason = CASE
    WHEN COALESCE(schema_name, '') = '' OR COALESCE(table_name, '') = ''
        THEN 'missing key: schema_name and table_name are required'
    WHEN schema_name GLOB '*[^a-z0-9_]*' OR table_name GLOB '*[^a-z0-9_]*'
        THEN 'invalid identifier'
    WHEN access_role IS NULL
        THEN 'missing access_role'
    WHEN access_role NOT IN (SELECT access_role FROM access_role)
        THEN 'unknown access_role ''' || access_role || ''''
    WHEN certified IS NULL
        THEN 'invalid certified flag ''' || certified_raw || ''' (expected Y/N)'
    WHEN NOT EXISTS (SELECT 1 FROM raw_technical_table r
                     WHERE r.run_id = :run_id
                       AND r.schema_name = stg_business_table.schema_name
                       AND r.table_name = stg_business_table.table_name)
        THEN 'orphan: table not found in warehouse'
END;

-- Duplicate keys inside one file: the last valid row wins, earlier ones are rejected.
-- (Across files a later file is an intentional override, not a duplicate.)
DROP TABLE IF EXISTS temp.tmp_dupe;
CREATE TEMP TABLE tmp_dupe AS
SELECT source_seq, winner_row FROM (
    SELECT source_seq,
           ROW_NUMBER() OVER w AS rn,
           FIRST_VALUE(source_row) OVER w AS winner_row
    FROM stg_business_table
    WHERE reject_reason IS NULL
    WINDOW w AS (PARTITION BY source_file, schema_name, table_name ORDER BY source_seq DESC)
) WHERE rn > 1;
UPDATE stg_business_table
SET reject_reason = 'duplicate key in file, superseded by row '
                    || (SELECT winner_row FROM tmp_dupe d WHERE d.source_seq = stg_business_table.source_seq)
WHERE source_seq IN (SELECT source_seq FROM tmp_dupe);

INSERT INTO quarantine (run_id, entity, source_file, source_row, record_key, reason, raw_record)
SELECT :run_id, 'business_table', s.source_file, s.source_row,
       COALESCE(s.schema_name, '?') || '.' || COALESCE(s.table_name, '?'), s.reject_reason,
       json_object('schema_name', r.schema_name, 'table_name', r.table_name,
                   'description', r.description, 'access_role', r.access_role, 'certified', r.certified)
FROM stg_business_table s
JOIN raw_business_table r ON r.run_id = :run_id AND r.source_seq = s.source_seq
WHERE s.reject_reason IS NOT NULL;

-- ------------------------------------------------- business column rows ----
DELETE FROM stg_business_column;
INSERT INTO stg_business_column (
    source_file, source_seq, source_row, schema_name, table_name, column_name, description,
    is_pii_raw, is_pii, access_role)
SELECT
    source_file, source_seq, source_row,
    LOWER(TRIM(schema_name)), LOWER(TRIM(table_name)), LOWER(TRIM(column_name)),
    NULLIF(TRIM(description), ''),
    is_pii,
    CASE
        WHEN TRIM(COALESCE(is_pii, '')) = '' THEN NULL       -- unknown, decided later
        WHEN UPPER(TRIM(is_pii)) IN ('Y', 'YES', 'TRUE', '1') THEN 1
        WHEN UPPER(TRIM(is_pii)) IN ('N', 'NO', 'FALSE', '0') THEN 0
        ELSE -1                                              -- unparseable
    END,
    NULLIF(LOWER(TRIM(access_role)), '')
FROM raw_business_column
WHERE run_id = :run_id;

UPDATE stg_business_column SET reject_reason = CASE
    WHEN COALESCE(schema_name, '') = '' OR COALESCE(table_name, '') = '' OR COALESCE(column_name, '') = ''
        THEN 'missing key: schema_name, table_name and column_name are required'
    WHEN schema_name GLOB '*[^a-z0-9_]*' OR table_name GLOB '*[^a-z0-9_]*' OR column_name GLOB '*[^a-z0-9_]*'
        THEN 'invalid identifier'
    WHEN access_role IS NOT NULL AND access_role NOT IN (SELECT access_role FROM access_role)
        THEN 'unknown access_role ''' || access_role || ''''
    WHEN is_pii = -1
        THEN 'invalid is_pii flag ''' || is_pii_raw || ''' (expected Y/N)'
    WHEN NOT EXISTS (SELECT 1 FROM raw_technical_column r
                     WHERE r.run_id = :run_id
                       AND r.schema_name = stg_business_column.schema_name
                       AND r.table_name = stg_business_column.table_name
                       AND r.column_name = stg_business_column.column_name)
        THEN 'orphan: column not found in warehouse'
END;

DROP TABLE IF EXISTS temp.tmp_dupe;
CREATE TEMP TABLE tmp_dupe AS
SELECT source_seq, winner_row FROM (
    SELECT source_seq,
           ROW_NUMBER() OVER w AS rn,
           FIRST_VALUE(source_row) OVER w AS winner_row
    FROM stg_business_column
    WHERE reject_reason IS NULL
    WINDOW w AS (PARTITION BY source_file, schema_name, table_name, column_name ORDER BY source_seq DESC)
) WHERE rn > 1;
UPDATE stg_business_column
SET reject_reason = 'duplicate key in file, superseded by row '
                    || (SELECT winner_row FROM tmp_dupe d WHERE d.source_seq = stg_business_column.source_seq)
WHERE source_seq IN (SELECT source_seq FROM tmp_dupe);

INSERT INTO quarantine (run_id, entity, source_file, source_row, record_key, reason, raw_record)
SELECT :run_id, 'business_column', s.source_file, s.source_row,
       COALESCE(s.schema_name, '?') || '.' || COALESCE(s.table_name, '?') || '.' || COALESCE(s.column_name, '?'),
       s.reject_reason,
       json_object('column_name', r.column_name, 'description', r.description,
                   'is_pii', r.is_pii, 'access_role', r.access_role)
FROM stg_business_column s
JOIN raw_business_column r ON r.run_id = :run_id AND r.source_seq = s.source_seq
WHERE s.reject_reason IS NOT NULL;

-- --------------------------------------------------------------- glossary ----
DELETE FROM stg_glossary;
INSERT INTO stg_glossary (source_file, source_seq, source_row, term, definition, synonyms, related_tables)
SELECT source_file, source_seq, source_row,
       LOWER(TRIM(term)), NULLIF(TRIM(definition), ''),
       NULLIF(LOWER(REPLACE(TRIM(synonyms), '; ', ';')), ''),
       NULLIF(LOWER(REPLACE(TRIM(related_tables), ' ', '')), '')
FROM raw_glossary
WHERE run_id = :run_id;

UPDATE stg_glossary SET reject_reason = CASE
    WHEN COALESCE(term, '') = '' THEN 'missing term'
    WHEN definition IS NULL THEN 'missing definition'
END;

DROP TABLE IF EXISTS temp.tmp_dupe;
CREATE TEMP TABLE tmp_dupe AS
SELECT source_seq, winner_row FROM (
    SELECT source_seq,
           ROW_NUMBER() OVER w AS rn,
           FIRST_VALUE(source_row) OVER w AS winner_row
    FROM stg_glossary
    WHERE reject_reason IS NULL
    WINDOW w AS (PARTITION BY source_file, term ORDER BY source_seq DESC)
) WHERE rn > 1;
UPDATE stg_glossary
SET reject_reason = 'duplicate key in file, superseded by row '
                    || (SELECT winner_row FROM tmp_dupe d WHERE d.source_seq = stg_glossary.source_seq)
WHERE source_seq IN (SELECT source_seq FROM tmp_dupe);

INSERT INTO quarantine (run_id, entity, source_file, source_row, record_key, reason, raw_record)
SELECT :run_id, 'glossary', s.source_file, s.source_row, COALESCE(s.term, '?'), s.reject_reason,
       json_object('term', r.term, 'definition', r.definition)
FROM stg_glossary s
JOIN raw_glossary r ON r.run_id = :run_id AND r.source_seq = s.source_seq
WHERE s.reject_reason IS NOT NULL;
