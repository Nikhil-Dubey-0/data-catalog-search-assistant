-- =============================================================================
-- Step 3: incremental refresh of the FTS5 search index.
-- Only tables touched in this run's change_log are re-indexed (plus everything on
-- a cold start, when the index is empty).  Inactive tables/columns are removed.
-- Parameter: :run_id
-- =============================================================================

DROP TABLE IF EXISTS temp.reindex_table;
CREATE TEMP TABLE reindex_table AS
SELECT DISTINCT table_key FROM change_log WHERE run_id = :run_id
UNION
SELECT table_key FROM catalog_table
WHERE NOT EXISTS (SELECT 1 FROM search_doc LIMIT 1);

DELETE FROM search_doc
WHERE table_key IN (SELECT table_key FROM reindex_table);

-- Table documents: name words weigh most, then description, then context.
INSERT INTO search_doc (doc_type, table_key, column_name, table_access_role, column_access_role,
                        name, description, context)
SELECT 'table', t.table_key, NULL, t.access_role, NULL,
       REPLACE(t.table_name, '_', ' '),
       COALESCE(t.description, ''),
       t.schema_name || ' ' || COALESCE(t.domain, '') || ' ' || REPLACE(COALESCE(t.tags, ''), ';', ' ')
FROM catalog_table t
WHERE t.is_active = 1
  AND t.table_key IN (SELECT table_key FROM reindex_table);

-- Column documents carry both the table role and the column's own role (if any).
INSERT INTO search_doc (doc_type, table_key, column_name, table_access_role, column_access_role,
                        name, description, context)
SELECT 'column', c.table_key, c.column_name, t.access_role, c.access_role,
       REPLACE(c.column_name, '_', ' '),
       COALESCE(c.description, ''),
       REPLACE(t.table_name, '_', ' ') || ' ' || t.schema_name
FROM catalog_column c
JOIN catalog_table t ON t.table_key = c.table_key
WHERE c.is_active = 1 AND t.is_active = 1
  AND c.table_key IN (SELECT table_key FROM reindex_table);
