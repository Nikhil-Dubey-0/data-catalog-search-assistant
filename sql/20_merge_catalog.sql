-- =============================================================================
-- Step 2: technical metadata (truth about what exists) + best steward row
--         -> desired state -> change_log -> UPSERT into curated catalog.
-- Re-running with unchanged inputs produces no change_log rows and no updates
-- other than last_seen_run / profiling stats.
-- Parameter: :run_id
-- =============================================================================

-- ------------------------------------------------------------ tables -------
DROP TABLE IF EXISTS temp.desired_table;
CREATE TEMP TABLE desired_table AS
WITH best AS (                     -- latest valid steward row per table (later files override)
    SELECT *, ROW_NUMBER() OVER (PARTITION BY schema_name, table_name ORDER BY source_seq DESC) AS rn
    FROM stg_business_table
    WHERE reject_reason IS NULL
),
col_counts AS (
    SELECT schema_name, table_name, COUNT(*) AS column_count
    FROM raw_technical_column WHERE run_id = :run_id
    GROUP BY schema_name, table_name
)
SELECT
    r.schema_name || '.' || r.table_name                       AS table_key,
    r.schema_name,
    r.table_name,
    b.description,
    b.owner,
    COALESCE(b.domain, r.schema_name)                          AS domain,
    b.tags,
    -- FAIL CLOSED: no valid steward classification -> admin-only
    COALESCE(b.access_role, 'restricted')                      AS access_role,
    CASE WHEN b.access_role IS NULL THEN 'default_restricted' ELSE 'steward' END AS access_source,
    b.refresh_frequency,
    COALESCE(b.certified, 0)                                   AS certified,
    r.row_count,
    COALESCE(cc.column_count, 0)                               AS column_count,
    CASE WHEN b.description IS NULL THEN 0 ELSE 1 END          AS is_documented
FROM raw_technical_table r
LEFT JOIN best b       ON b.schema_name = r.schema_name AND b.table_name = r.table_name AND b.rn = 1
LEFT JOIN col_counts cc ON cc.schema_name = r.schema_name AND cc.table_name = r.table_name
WHERE r.run_id = :run_id;

INSERT INTO change_log (run_id, entity_type, entity_key, table_key, change_type, changed_fields)
SELECT :run_id, 'table', d.table_key, d.table_key,
       CASE WHEN c.table_key IS NULL THEN 'added' ELSE 'reactivated' END, NULL
FROM desired_table d
LEFT JOIN catalog_table c ON c.table_key = d.table_key
WHERE c.table_key IS NULL OR c.is_active = 0;

INSERT INTO change_log (run_id, entity_type, entity_key, table_key, change_type, changed_fields)
SELECT :run_id, 'table', d.table_key, d.table_key, 'modified',
       RTRIM(
           CASE WHEN c.description IS NOT d.description THEN 'description, ' ELSE '' END ||
           CASE WHEN c.owner IS NOT d.owner THEN 'owner, ' ELSE '' END ||
           CASE WHEN c.domain IS NOT d.domain THEN 'domain, ' ELSE '' END ||
           CASE WHEN c.tags IS NOT d.tags THEN 'tags, ' ELSE '' END ||
           CASE WHEN c.access_role IS NOT d.access_role
                THEN 'access_role (' || c.access_role || ' -> ' || d.access_role || '), ' ELSE '' END ||
           CASE WHEN c.refresh_frequency IS NOT d.refresh_frequency THEN 'refresh_frequency, ' ELSE '' END ||
           CASE WHEN c.certified IS NOT d.certified THEN 'certified, ' ELSE '' END ||
           CASE WHEN c.column_count IS NOT d.column_count
                THEN 'column_count (' || c.column_count || ' -> ' || d.column_count || '), ' ELSE '' END,
           ', ')
FROM desired_table d
JOIN catalog_table c ON c.table_key = d.table_key AND c.is_active = 1
WHERE c.description IS NOT d.description OR c.owner IS NOT d.owner OR c.domain IS NOT d.domain
   OR c.tags IS NOT d.tags OR c.access_role IS NOT d.access_role
   OR c.refresh_frequency IS NOT d.refresh_frequency OR c.certified IS NOT d.certified
   OR c.column_count IS NOT d.column_count;

INSERT INTO change_log (run_id, entity_type, entity_key, table_key, change_type, changed_fields)
SELECT :run_id, 'table', c.table_key, c.table_key, 'removed', 'no longer in warehouse'
FROM catalog_table c
WHERE c.is_active = 1 AND c.table_key NOT IN (SELECT table_key FROM desired_table);

INSERT INTO catalog_table (
    table_key, schema_name, table_name, description, owner, domain, tags, access_role, access_source,
    refresh_frequency, certified, row_count, column_count, is_documented, is_active,
    first_seen_run, last_changed_run, last_seen_run)
SELECT table_key, schema_name, table_name, description, owner, domain, tags, access_role, access_source,
       refresh_frequency, certified, row_count, column_count, is_documented, 1,
       :run_id, :run_id, :run_id
FROM desired_table WHERE TRUE
ON CONFLICT (table_key) DO UPDATE SET
    description       = excluded.description,
    owner             = excluded.owner,
    domain            = excluded.domain,
    tags              = excluded.tags,
    access_role       = excluded.access_role,
    access_source     = excluded.access_source,
    refresh_frequency = excluded.refresh_frequency,
    certified         = excluded.certified,
    row_count         = excluded.row_count,
    column_count      = excluded.column_count,
    is_documented     = excluded.is_documented,
    is_active         = 1,
    last_seen_run     = excluded.last_seen_run,
    last_changed_run  = CASE
        WHEN catalog_table.table_key IN (SELECT entity_key FROM change_log
                                         WHERE run_id = :run_id AND entity_type = 'table')
        THEN excluded.last_changed_run ELSE catalog_table.last_changed_run END;

UPDATE catalog_table
SET is_active = 0, last_changed_run = :run_id
WHERE is_active = 1 AND table_key NOT IN (SELECT table_key FROM desired_table);

-- ----------------------------------------------------------- columns -------
DROP TABLE IF EXISTS temp.desired_column;
CREATE TEMP TABLE desired_column AS
WITH best AS (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY schema_name, table_name, column_name ORDER BY source_seq DESC) AS rn
    FROM stg_business_column
    WHERE reject_reason IS NULL
),
joined AS (
    SELECT r.*, b.description, b.is_pii AS steward_pii, b.access_role AS steward_role,
           -- Fail-closed name rule, only used when no steward has classified the column.
           CASE WHEN b.is_pii IS NULL AND (
                    r.column_name LIKE '%email%' OR r.column_name LIKE '%phone%'
                 OR r.column_name LIKE '%bank%'  OR r.column_name LIKE '%ssn%'
                 OR r.column_name LIKE '%passport%' OR r.column_name LIKE '%birth%'
                 OR r.column_name LIKE '%address%')
                THEN 1 ELSE 0 END AS name_rule_pii
    FROM raw_technical_column r
    LEFT JOIN best b ON b.schema_name = r.schema_name AND b.table_name = r.table_name
                    AND b.column_name = r.column_name AND b.rn = 1
    WHERE r.run_id = :run_id
)
SELECT
    schema_name || '.' || table_name                              AS table_key,
    schema_name || '.' || table_name || '.' || column_name        AS column_key,
    column_name, ordinal, data_type, is_nullable, is_primary_key, description,
    CASE WHEN steward_pii = 1 OR name_rule_pii = 1 THEN 1 ELSE 0 END AS is_pii,
    CASE WHEN steward_role IS NOT NULL THEN steward_role
         WHEN steward_pii = 1 OR name_rule_pii = 1 THEN 'pii'
    END                                                           AS access_role,
    CASE WHEN steward_role IS NOT NULL THEN 'steward'
         WHEN steward_pii = 1 THEN 'pii_flag'
         WHEN name_rule_pii = 1 THEN 'auto_pii_name_rule'
         ELSE 'inherited'
    END                                                           AS access_source,
    null_fraction, distinct_count,
    CASE WHEN description IS NULL THEN 0 ELSE 1 END               AS is_documented
FROM joined;

INSERT INTO change_log (run_id, entity_type, entity_key, table_key, change_type, changed_fields)
SELECT :run_id, 'column', d.column_key, d.table_key,
       CASE WHEN c.column_key IS NULL THEN 'added' ELSE 'reactivated' END, NULL
FROM desired_column d
LEFT JOIN catalog_column c ON c.column_key = d.column_key
WHERE c.column_key IS NULL OR c.is_active = 0;

INSERT INTO change_log (run_id, entity_type, entity_key, table_key, change_type, changed_fields)
SELECT :run_id, 'column', d.column_key, d.table_key, 'modified',
       RTRIM(
           CASE WHEN c.description IS NOT d.description THEN 'description, ' ELSE '' END ||
           CASE WHEN c.data_type IS NOT d.data_type
                THEN 'data_type (' || c.data_type || ' -> ' || d.data_type || '), ' ELSE '' END ||
           CASE WHEN c.is_nullable IS NOT d.is_nullable THEN 'is_nullable, ' ELSE '' END ||
           CASE WHEN c.is_pii IS NOT d.is_pii THEN 'is_pii, ' ELSE '' END ||
           CASE WHEN c.access_role IS NOT d.access_role
                THEN 'access_role (' || COALESCE(c.access_role, 'inherit') || ' -> '
                     || COALESCE(d.access_role, 'inherit') || '), ' ELSE '' END,
           ', ')
FROM desired_column d
JOIN catalog_column c ON c.column_key = d.column_key AND c.is_active = 1
WHERE c.description IS NOT d.description OR c.data_type IS NOT d.data_type
   OR c.is_nullable IS NOT d.is_nullable OR c.is_pii IS NOT d.is_pii
   OR c.access_role IS NOT d.access_role;

INSERT INTO change_log (run_id, entity_type, entity_key, table_key, change_type, changed_fields)
SELECT :run_id, 'column', c.column_key, c.table_key, 'removed', 'no longer in warehouse'
FROM catalog_column c
WHERE c.is_active = 1 AND c.column_key NOT IN (SELECT column_key FROM desired_column);

INSERT INTO catalog_column (
    column_key, table_key, column_name, ordinal, data_type, is_nullable, is_primary_key, description,
    is_pii, access_role, access_source, null_fraction, distinct_count, is_documented, is_active,
    first_seen_run, last_changed_run, last_seen_run)
SELECT column_key, table_key, column_name, ordinal, data_type, is_nullable, is_primary_key, description,
       is_pii, access_role, access_source, null_fraction, distinct_count, is_documented, 1,
       :run_id, :run_id, :run_id
FROM desired_column WHERE TRUE
ON CONFLICT (column_key) DO UPDATE SET
    ordinal          = excluded.ordinal,
    data_type        = excluded.data_type,
    is_nullable      = excluded.is_nullable,
    is_primary_key   = excluded.is_primary_key,
    description      = excluded.description,
    is_pii           = excluded.is_pii,
    access_role      = excluded.access_role,
    access_source    = excluded.access_source,
    null_fraction    = excluded.null_fraction,
    distinct_count   = excluded.distinct_count,
    is_documented    = excluded.is_documented,
    is_active        = 1,
    last_seen_run    = excluded.last_seen_run,
    last_changed_run = CASE
        WHEN catalog_column.column_key IN (SELECT entity_key FROM change_log
                                           WHERE run_id = :run_id AND entity_type = 'column')
        THEN excluded.last_changed_run ELSE catalog_column.last_changed_run END;

UPDATE catalog_column
SET is_active = 0, last_changed_run = :run_id
WHERE is_active = 1 AND column_key NOT IN (SELECT column_key FROM desired_column);

-- ---------------------------------------------------------- glossary -------
-- Small reference set: replace with the latest valid row per term.
DELETE FROM glossary_term;
INSERT INTO glossary_term (term, definition, synonyms, related_tables, last_run)
SELECT term, definition, synonyms, related_tables, :run_id
FROM (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY term ORDER BY source_seq DESC) AS rn
    FROM stg_glossary WHERE reject_reason IS NULL
) WHERE rn = 1;
