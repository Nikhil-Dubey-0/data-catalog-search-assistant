"""Access control: table- and column-level filtering, and nothing restricted reaches the model."""

from __future__ import annotations

import json

import pytest

from catalog_assistant.assistant import CatalogAssistant
from catalog_assistant.search import UnknownRoleError, search, visible_columns, visible_tables


def test_roles_see_only_granted_tables(con):
    analyst = {t["table_key"] for t in visible_tables(con, "analyst")}
    finance = {t["table_key"] for t in visible_tables(con, "finance_analyst")}
    admin = {t["table_key"] for t in visible_tables(con, "admin")}
    assert not any(k.startswith(("finance.", "hr.")) for k in analyst)
    assert "finance.revenue_monthly" in finance and not any(k.startswith("hr.") for k in finance)
    assert len(admin) == 16                                    # admin also sees the fail-closed table
    assert "analytics.daily_sales_summary" not in analyst | finance


def test_viewer_unknown_grant_was_dropped_not_widened(con):
    grants = {r[0] for r in con.execute("SELECT access_role FROM role_grant WHERE user_role = 'viewer'")}
    assert grants == {"public"}


def test_pii_columns_hidden_unless_granted(con):
    analyst_cols = {c["column_name"] for c in visible_columns(con, "sales.customers", "analyst")}
    admin_cols = {c["column_name"] for c in visible_columns(con, "sales.customers", "admin")}
    assert {"email", "phone", "full_name"} & analyst_cols == set()
    assert {"email", "phone", "full_name"} <= admin_cols
    # steward override: hr_partner may see employee names, but not bank details
    assert "full_name" in {c["column_name"] for c in visible_columns(con, "hr.employees", "hr_partner")}
    assert "bank_account_last4" not in {c["column_name"] for c in visible_columns(con, "hr.payroll", "hr_partner")}


def test_search_never_returns_unauthorised_tables_or_columns(con):
    res = search(con, "employee salary payroll revenue invoices customer email", "analyst", limit=10)
    keys = {r.table_key for r in res.results}
    assert keys and not any(k.startswith(("hr.", "finance.")) for k in keys)
    for r in res.results:
        assert not any(c.column_name in {"email", "phone", "full_name"} and r.table_key == "sales.customers"
                       for c in r.matched_columns)


def test_unknown_role_is_rejected(con):
    with pytest.raises(UnknownRoleError, match="Known roles"):
        search(con, "revenue", "ceo")
    with pytest.raises(UnknownRoleError):
        CatalogAssistant(con, None, log_queries=False).ask("revenue", "")


def test_model_prompt_contains_only_authorised_metadata(con, scripted):
    llm = scripted('{"keywords": ["salary", "revenue", "email"]}', "not json")
    CatalogAssistant(con, llm, log_queries=False).ask(
        "customer email, employee salaries and monthly revenue", "analyst")
    answer_prompt = llm.calls[1]["user"]
    evidence = json.loads(answer_prompt.split("CATALOG_EVIDENCE:\n", 1)[1])
    tables = {e["table_key"] for e in evidence}
    assert tables and not any(t.startswith(("hr.", "finance.")) for t in tables)
    for e in evidence:
        if e["table_key"] == "sales.customers":
            assert {"email", "phone", "full_name"} & {c["name"] for c in e["columns"]} == set()
