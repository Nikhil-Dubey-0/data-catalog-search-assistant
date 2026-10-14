-- =============================================================================
-- Consumption views.  Access rules live here (and in search.py, which uses the
-- same role_grant join) so every consumer applies the same policy.
-- =============================================================================

-- Tables a user role may see: the table's access_role must be granted to the role.
DROP VIEW IF EXISTS v_role_visible_table;
CREATE VIEW v_role_visible_table AS
SELECT g.user_role, t.*
FROM catalog_table t
JOIN role_grant g ON g.access_role = t.access_role
WHERE t.is_active = 1;

-- Columns a user role may see: table visible AND the column's effective role granted.
-- A column with its own access_role (e.g. 'pii') needs that role in addition.
DROP VIEW IF EXISTS v_role_visible_column;
CREATE VIEW v_role_visible_column AS
SELECT vt.user_role, c.*
FROM catalog_column c
JOIN v_role_visible_table vt ON vt.table_key = c.table_key
JOIN role_grant g ON g.user_role = vt.user_role
                 AND g.access_role = COALESCE(c.access_role, vt.access_role)
WHERE c.is_active = 1;

-- Documentation coverage per active table (used by the "metadata health" report).
DROP VIEW IF EXISTS v_metadata_quality;
CREATE VIEW v_metadata_quality AS
SELECT
    t.table_key,
    t.owner,
    t.access_role,
    t.access_source,
    t.is_documented                                         AS table_documented,
    COUNT(c.column_key)                                     AS active_columns,
    SUM(CASE WHEN c.is_documented = 0 THEN 1 ELSE 0 END)    AS undocumented_columns,
    SUM(CASE WHEN c.is_pii = 1 THEN 1 ELSE 0 END)           AS pii_columns,
    ROUND(100.0 * SUM(c.is_documented) / NULLIF(COUNT(c.column_key), 0), 1) AS documented_pct
FROM catalog_table t
LEFT JOIN catalog_column c ON c.table_key = t.table_key AND c.is_active = 1
WHERE t.is_active = 1
GROUP BY t.table_key;

-- One row per pipeline run with change and quarantine counts.
DROP VIEW IF EXISTS v_run_summary;
CREATE VIEW v_run_summary AS
SELECT
    r.run_id, r.started_at, r.status,
    (SELECT COUNT(*) FROM change_log cl WHERE cl.run_id = r.run_id AND cl.change_type = 'added')    AS added,
    (SELECT COUNT(*) FROM change_log cl WHERE cl.run_id = r.run_id AND cl.change_type = 'modified') AS modified,
    (SELECT COUNT(*) FROM change_log cl WHERE cl.run_id = r.run_id AND cl.change_type = 'removed')  AS removed,
    (SELECT COUNT(*) FROM quarantine q WHERE q.run_id = r.run_id)                                   AS quarantined,
    r.error
FROM etl_run r;
