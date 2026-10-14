"""Retrieval behaviour and input edge cases."""

from __future__ import annotations

import pytest

from catalog_assistant.search import InvalidQueryError, build_term_groups, search


def test_glossary_bridges_vocabulary_gap(con):
    with_glossary = search(con, "client attrition figures", "analyst")
    without = search(con, "client attrition figures", "analyst", use_glossary=False)
    assert with_glossary.results[0].table_key == "analytics.customer_churn_monthly"
    assert with_glossary.results[0].confidence == "high"
    assert not without.results or without.results[0].table_key != "analytics.customer_churn_monthly"


def test_results_carry_column_evidence(con):
    top = search(con, "churn rate by region", "analyst").results[0]
    assert top.table_key == "analytics.customer_churn_monthly"
    names = {c.column_name for c in top.matched_columns}
    assert {"churn_rate", "region"} <= names
    assert all(c.description for c in top.matched_columns if c.column_name in {"churn_rate", "region"})


@pytest.mark.parametrize("q", ['"', "AND OR NOT", "NEAR(", "* ^ :", "'; DROP TABLE catalog_table; --",
                               "revenue\" OR table_access_role:restricted"])
def test_fts_syntax_and_injection_attempts_are_harmless(con, q):
    res = search(con, q, "viewer")
    assert all(r.access_role == "public" for r in res.results)
    assert con.execute("SELECT COUNT(*) FROM catalog_table").fetchone()[0] == 16


def test_empty_and_oversized_questions_are_rejected(con):
    with pytest.raises(InvalidQueryError):
        search(con, "   ", "analyst")
    with pytest.raises(InvalidQueryError):
        search(con, "x" * 501, "analyst")


def test_stopword_only_question_returns_nothing(con):
    assert search(con, "where can I find the data?", "admin").results == []


def test_unrelated_question_has_no_match(con):
    assert search(con, "weather forecast for tomorrow", "admin").results == []


def test_term_groups_merge_multiword_synonyms():
    glossary = [{"term": "ticket", "synonyms": "support case;complaint", "related_tables": "", "definition": "x"}]
    groups = build_term_groups("open support cases by priority", glossary)
    assert groups[0].label == "ticket" and groups[0].source == "glossary"
    assert [g.label for g in groups[1:]] == ["open", "priority"]
