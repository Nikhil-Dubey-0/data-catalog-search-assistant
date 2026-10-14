"""Data engineering: ingestion, validation/quarantine, idempotency, incremental changes, atomicity."""

from __future__ import annotations

import shutil

import pytest

from catalog_assistant import db
from catalog_assistant.config import CHANGE_BATCH_DIR, DEFAULT_METADATA_DIR, Settings
from catalog_assistant.harvest import WarehouseNotFoundError
from catalog_assistant.ingest import MetadataSchemaError
from catalog_assistant.pipeline import run_build
from catalog_assistant.warehouse import simulate_schema_change


def counts(c):
    return (c.execute("SELECT COUNT(*) FROM catalog_table").fetchone()[0],
            c.execute("SELECT COUNT(*) FROM catalog_column").fetchone()[0],
            c.execute("SELECT COUNT(*) FROM search_doc").fetchone()[0])


def test_first_build_loads_all_warehouse_tables(con):
    assert con.execute("SELECT COUNT(*) FROM catalog_table WHERE is_active = 1").fetchone()[0] == 16
    assert con.execute("SELECT COUNT(*) FROM catalog_column WHERE is_active = 1").fetchone()[0] == 99
    # one search document per table and per column
    assert con.execute("SELECT COUNT(*) FROM search_doc").fetchone()[0] == 16 + 99


def test_bad_steward_rows_are_quarantined_with_reasons(con):
    reasons = {(r["record_key"], r["reason"]) for r in con.execute("SELECT record_key, reason FROM quarantine")}
    expected = {
        ("analytics.daily_sales_summary", "unknown access_role 'superuser'"),
        ("sales.legacy_orders_2019", "orphan: table not found in warehouse"),
        ("product.products", "invalid certified flag 'maybe' (expected Y/N)"),
        ("sales.customers.loyalty_points", "orphan: column not found in warehouse"),
        ("marketing.web_sessions.visitor_id", "unknown access_role 'secret'"),
        ("support.tickets.priority", "invalid is_pii flag 'maybe' (expected Y/N)"),
        ("", "missing term"),
    }
    assert expected <= reasons
    dupes = [r for r in reasons if r[1].startswith("duplicate key")]
    assert {d[0] for d in dupes} >= {"support.tickets", "sales.orders", "product.categories",
                                     "sales.orders.total_amount", "revenue"}


def test_duplicates_resolve_to_last_valid_row(con):
    desc = con.execute("SELECT description FROM catalog_table WHERE table_key = 'support.tickets'").fetchone()[0]
    assert desc.startswith("Customer support tickets")  # not the earlier "(old description)" row
    col = con.execute("SELECT description FROM catalog_column WHERE column_key = 'sales.orders.total_amount'").fetchone()[0]
    assert col.startswith("Total order value")
    # whitespace/case variant '  SALES ,  Orders ' was standardised, not loaded as a new table
    assert con.execute("SELECT COUNT(*) FROM catalog_table WHERE lower(table_key) LIKE '%orders%'").fetchone()[0] == 1


def test_unclassified_table_fails_closed(con):
    row = con.execute("SELECT access_role, access_source FROM catalog_table "
                      "WHERE table_key = 'analytics.daily_sales_summary'").fetchone()
    assert tuple(row) == ("restricted", "default_restricted")


def test_null_description_is_flagged_not_rejected(con):
    row = con.execute("SELECT is_documented, access_role FROM catalog_table "
                      "WHERE table_key = 'product.categories'").fetchone()
    assert tuple(row) == (0, "public")


def test_rerun_with_same_inputs_is_skipped_and_forced_rerun_changes_nothing(fresh_settings):
    c = db.connect(fresh_settings.catalog_db)
    before = counts(c)
    assert run_build(fresh_settings).status == "skipped"
    forced = run_build(fresh_settings, force=True)
    assert forced.status == "success"
    assert forced.stats["changes"] == {}                       # no change_log rows
    assert counts(c) == before                                 # no duplicated rows anywhere
    assert c.execute("SELECT COUNT(*) FROM change_log WHERE run_id = ?", (forced.run_id,)).fetchone()[0] == 0


def test_incremental_schema_change_and_new_metadata(fresh_settings):
    simulate_schema_change(fresh_settings.warehouse_dir)
    assert simulate_schema_change(fresh_settings.warehouse_dir) == []      # idempotent
    res = run_build(fresh_settings, [DEFAULT_METADATA_DIR, CHANGE_BATCH_DIR])
    assert res.status == "success"
    c = db.connect(fresh_settings.catalog_db)
    log = {(r["entity_key"], r["change_type"]) for r in
           c.execute("SELECT entity_key, change_type FROM change_log WHERE run_id = ?", (res.run_id,))}
    assert ("marketing.email_events", "added") in log
    assert ("sales.customers.loyalty_tier", "added") in log
    assert ("support.csat_responses", "removed") in log
    assert ("analytics.daily_sales_summary", "modified") in log
    assert ("finance.revenue_monthly", "modified") in log

    # metadata fix applied: the fail-closed table is now public
    assert c.execute("SELECT access_role FROM catalog_table WHERE table_key = 'analytics.daily_sales_summary'"
                     ).fetchone()[0] == "public"
    # new undocumented e-mail column was auto-classified as PII (fail closed)
    row = c.execute("SELECT is_pii, access_role, access_source FROM catalog_column "
                    "WHERE column_key = 'marketing.email_events.recipient_email'").fetchone()
    assert tuple(row) == (1, "pii", "auto_pii_name_rule")
    # dropped table is soft-deleted and removed from the search index
    assert c.execute("SELECT is_active FROM catalog_table WHERE table_key = 'support.csat_responses'").fetchone()[0] == 0
    assert c.execute("SELECT COUNT(*) FROM search_doc WHERE table_key = 'support.csat_responses'").fetchone()[0] == 0
    # untouched tables were not re-indexed / re-stamped
    assert c.execute("SELECT last_changed_run FROM catalog_table WHERE table_key = 'hr.employees'").fetchone()[0] == 1
    assert run_build(fresh_settings, [DEFAULT_METADATA_DIR, CHANGE_BATCH_DIR]).status == "skipped"


def test_missing_required_header_fails_run_and_keeps_catalog(fresh_settings, tmp_path):
    bad = tmp_path / "bad_metadata"
    shutil.copytree(DEFAULT_METADATA_DIR, bad)
    f = bad / "business_tables.csv"
    lines = f.read_text(encoding="utf-8").splitlines()
    lines[0] = lines[0].replace("access_role", "access_level")   # schema drift in the steward file
    f.write_text("\n".join(lines), encoding="utf-8")

    c = db.connect(fresh_settings.catalog_db)
    before = counts(c)
    with pytest.raises(MetadataSchemaError, match="missing required column"):
        run_build(fresh_settings, [bad])
    last = c.execute("SELECT status, error FROM etl_run ORDER BY run_id DESC LIMIT 1").fetchone()
    assert last["status"] == "failed" and "access_role" in last["error"]
    assert counts(c) == before


def test_failure_mid_transform_rolls_back_everything(fresh_settings, monkeypatch):
    simulate_schema_change(fresh_settings.warehouse_dir)
    real = db.run_sql_file

    def flaky(con, name, params=None):
        if name.startswith("30_"):
            raise RuntimeError("disk full (simulated)")
        return real(con, name, params)

    monkeypatch.setattr(db, "run_sql_file", flaky)
    with pytest.raises(RuntimeError, match="simulated"):
        run_build(fresh_settings)
    c = db.connect(fresh_settings.catalog_db)
    # merge step had run inside the transaction, but nothing was committed
    assert c.execute("SELECT is_active FROM catalog_table WHERE table_key = 'support.csat_responses'").fetchone()[0] == 1
    assert c.execute("SELECT COUNT(*) FROM catalog_table WHERE table_key = 'marketing.email_events'").fetchone()[0] == 0
    assert c.execute("SELECT status FROM etl_run ORDER BY run_id DESC LIMIT 1").fetchone()[0] == "failed"


def test_missing_warehouse_is_a_clear_error(tmp_path):
    with pytest.raises(WarehouseNotFoundError, match="init-warehouse"):
        run_build(Settings(var_dir=tmp_path / "empty"))


def test_sql_splitter_keeps_semicolons_inside_strings():
    stmts = list(db.iter_statements("SELECT 'a;b';\n-- comment; here\nSELECT 2;\n"))
    assert len(stmts) == 2 and "'a;b'" in stmts[0]
