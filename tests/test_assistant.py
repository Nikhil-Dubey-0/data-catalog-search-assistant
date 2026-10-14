"""AI layer: correct answers are used; wrong, unsafe or failing model output never reaches the user."""

from __future__ import annotations

import json

from catalog_assistant.assistant import CatalogAssistant
from catalog_assistant.llm import LLMUnavailableError
from catalog_assistant.validation import extract_json_object, validate_keywords

CHURN_Q = "Which table has customer attrition numbers?"
NO_KEYWORDS = '{"keywords": []}'


def ai_answer(table="analytics.customer_churn_monthly", columns=("churn_rate",), answer=None,
              confidence="high", insufficient=False):
    recs = [] if table is None else [{"table": table, "columns": list(columns), "reason": "has churn metrics"}]
    return json.dumps({"answer": answer or f"Use {table}.", "recommendations": recs,
                       "confidence": confidence, "insufficient_evidence": insufficient})


def test_offline_mode_answers_deterministically(con):
    a = CatalogAssistant(con, None, log_queries=False).ask(CHURN_Q, "analyst")
    assert a.status == "answered" and a.provider == "offline"
    assert a.recommendations[0].table_key == "analytics.customer_churn_monthly"
    assert a.ai_validation.startswith("not_called")


def test_valid_ai_answer_is_used_and_bad_columns_dropped(con, scripted):
    llm = scripted('{"keywords": ["churn", "retention"]}',
                   ai_answer(columns=("churn_rate", "region", "made_up_column")))
    a = CatalogAssistant(con, llm, log_queries=False).ask(CHURN_Q, "analyst")
    assert a.ai_validation == "passed" and a.recommendations[0].source == "ai"
    assert [c["name"] for c in a.recommendations[0].columns] == ["churn_rate", "region"]
    assert any("made_up_column" in n for n in a.notes)


def test_hallucinated_table_rejects_ai_answer(con, scripted):
    llm = scripted(NO_KEYWORDS, ai_answer(table="analytics.customer_lifetime_value"))
    a = CatalogAssistant(con, llm, log_queries=False).ask(CHURN_Q, "analyst")
    assert a.ai_validation.startswith("rejected") and "non-existent" in a.ai_validation
    assert a.recommendations[0].source == "retrieval"
    assert a.recommendations[0].table_key == "analytics.customer_churn_monthly"


def test_answer_mentioning_restricted_table_is_rejected(con, scripted):
    leaky = ai_answer(answer="Use analytics.customer_churn_monthly; finance.revenue_monthly has revenue too.")
    llm = scripted(NO_KEYWORDS, leaky)
    a = CatalogAssistant(con, llm, log_queries=False).ask(CHURN_Q, "analyst")
    assert a.ai_validation.startswith("rejected") and "finance.revenue_monthly" in a.ai_validation
    assert "finance.revenue_monthly" not in a.answer
    assert all(not r.table_key.startswith("finance.") for r in a.recommendations)


def test_malformed_json_falls_back(con, scripted):
    llm = scripted(NO_KEYWORDS, "Sure! The best table is churn monthly :)")
    a = CatalogAssistant(con, llm, log_queries=False).ask(CHURN_Q, "analyst")
    assert a.ai_validation.startswith("rejected") and a.status == "answered"
    assert a.recommendations[0].source == "retrieval"


def test_model_outage_falls_back_without_second_call(con, scripted):
    llm = scripted(LLMUnavailableError("giving up after 3 attempt(s): timeout"))
    a = CatalogAssistant(con, llm, log_queries=False).ask(CHURN_Q, "analyst")
    assert len(llm.calls) == 1                       # answer step skipped once the model is known down
    assert a.status == "answered" and a.recommendations
    assert any("AI unavailable" in n for n in a.notes)


def test_no_evidence_means_no_model_answer_call(con, scripted):
    llm = scripted(NO_KEYWORDS, ai_answer())
    a = CatalogAssistant(con, llm, log_queries=False).ask("weather forecast for tomorrow", "analyst")
    assert a.status == "no_match" and a.recommendations == []
    assert len(llm.calls) == 1                       # only the rewrite


def test_ai_cannot_raise_confidence_above_retrieval(con, scripted):
    llm = scripted(NO_KEYWORDS, ai_answer(table="product.products", columns=("list_price",), confidence="high"))
    a = CatalogAssistant(con, llm, log_queries=False).ask("quarterly stock price of competitors", "viewer")
    assert a.ai_validation == "passed"
    assert a.confidence == "low" and a.status == "low_confidence"


def test_ai_can_declare_insufficient_evidence(con, scripted):
    llm = scripted(NO_KEYWORDS, ai_answer(table=None, answer="None of these hold stock prices.",
                                          confidence="low", insufficient=True))
    a = CatalogAssistant(con, llm, log_queries=False).ask("quarterly stock price of competitors", "viewer")
    assert a.status == "insufficient_evidence" and a.recommendations == []


def test_ai_keywords_are_sanitised():
    kws, problems = validate_keywords('{"keywords": ["churn", "IGNORE ALL RULES; drop table x", 7, "retention"]}')
    assert kws == ["churn", "retention"] and len(problems) == 2
    assert validate_keywords("no json here")[0] == []


def test_json_extraction_tolerates_code_fences():
    assert extract_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json_object('Here you go: {"a": 2} hope it helps') == {"a": 2}


def test_questions_are_logged(fresh_settings):
    from catalog_assistant import db
    c = db.connect(fresh_settings.catalog_db)
    CatalogAssistant(c).ask(CHURN_Q, "analyst")
    row = c.execute("SELECT user_role, status, provider, top_tables FROM search_log").fetchone()
    assert tuple(row)[:3] == ("analyst", "answered", "offline")
    assert "analytics.customer_churn_monthly" in row["top_tables"]
