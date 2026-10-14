# Testing evidence

All results below were produced on Windows 11, Python 3.13.1, from this repository. Re-generate with:
`pytest -v`, `python -m catalog_assistant eval --output docs/eval_report.md`, `bash scripts/demo.sh`.

## 1. Clean-setup verification

A fresh `git clone` into an empty folder, a new virtual environment, `pip install -r requirements.txt`, then:

```
$ python -m catalog_assistant setup
run 1: SUCCESS
  active tables/columns : 16 / 99
  changes               : {'column_added': 99, 'table_added': 16}
  quarantined rows      : {'business_column': 4, 'business_table': 6, 'glossary': 2, 'user_role_grant': 1}
  undocumented tbl/col  : 2 / 8
  fail-closed tables    : 1   auto-PII columns: 0
$ pytest -q
.................................................                        [100%]
49 passed
```

No network or API key is needed: LLM behaviour is tested with a scripted fake model and `httpx.MockTransport`.

## 2. Automated tests (49)

| File | Tests | Failure / edge cases covered |
|---|---|---|
| `test_pipeline.py` | 11 | duplicates, invalid flags, unknown roles, orphans, missing term; fail-closed table; idempotent + forced rerun; incremental schema change; **header drift fails run, catalog unchanged**; **mid-transform failure rolls back**; missing warehouse |
| `test_access.py` | 6 | role/table filtering; PII column masking; unknown grant not applied; unknown role; **prompt contains no restricted metadata** |
| `test_search.py` | 12 | glossary vs no glossary; evidence; **6 FTS syntax / SQL injection strings**; empty, oversized, stopword-only, unrelated questions |
| `test_assistant.py` | 12 | valid AI answer; **hallucinated table**; **restricted table in answer text**; **malformed JSON**; **model outage**; no evidence → no model call; confidence capping; insufficient evidence; keyword sanitising; code fences; query logging |
| `test_llm_clients.py` | 7 | Ollama / OpenAI-compatible request format; **retry on 503 + timeout**; no retry on 401; give up after N attempts; bad response shape; provider factory |
| `test_eval.py` | 1 | quality gate: 0 leaks, pass rate ≥ 90 %, glossary ≥ FTS-only |

```
tests/test_access.py::test_roles_see_only_granted_tables PASSED          [  2%]
tests/test_access.py::test_viewer_unknown_grant_was_dropped_not_widened PASSED [  4%]
tests/test_access.py::test_pii_columns_hidden_unless_granted PASSED      [  6%]
tests/test_access.py::test_search_never_returns_unauthorised_tables_or_columns PASSED [  8%]
tests/test_access.py::test_unknown_role_is_rejected PASSED               [ 10%]
tests/test_access.py::test_model_prompt_contains_only_authorised_metadata PASSED [ 12%]
tests/test_assistant.py::test_offline_mode_answers_deterministically PASSED [ 14%]
tests/test_assistant.py::test_valid_ai_answer_is_used_and_bad_columns_dropped PASSED [ 16%]
tests/test_assistant.py::test_hallucinated_table_rejects_ai_answer PASSED [ 18%]
tests/test_assistant.py::test_answer_mentioning_restricted_table_is_rejected PASSED [ 20%]
tests/test_assistant.py::test_malformed_json_falls_back PASSED           [ 22%]
tests/test_assistant.py::test_model_outage_falls_back_without_second_call PASSED [ 24%]
tests/test_assistant.py::test_no_evidence_means_no_model_answer_call PASSED [ 26%]
tests/test_assistant.py::test_ai_cannot_raise_confidence_above_retrieval PASSED [ 28%]
tests/test_assistant.py::test_ai_can_declare_insufficient_evidence PASSED [ 30%]
tests/test_assistant.py::test_ai_keywords_are_sanitised PASSED           [ 32%]
tests/test_assistant.py::test_json_extraction_tolerates_code_fences PASSED [ 34%]
tests/test_assistant.py::test_questions_are_logged PASSED                [ 36%]
tests/test_eval.py::test_eval_set_has_zero_access_leaks_and_high_pass_rate PASSED [ 38%]
tests/test_llm_clients.py::test_ollama_sends_schema_and_parses_message PASSED [ 40%]
tests/test_llm_clients.py::test_openai_compatible_sends_bearer_token_and_json_mode PASSED [ 42%]
tests/test_llm_clients.py::test_transient_errors_are_retried_then_succeed PASSED [ 44%]
tests/test_llm_clients.py::test_permanent_error_is_not_retried PASSED    [ 46%]
tests/test_llm_clients.py::test_gives_up_after_max_retries PASSED        [ 48%]
tests/test_llm_clients.py::test_unexpected_response_shape_is_an_error PASSED [ 51%]
tests/test_llm_clients.py::test_factory PASSED                           [ 53%]
tests/test_pipeline.py::test_first_build_loads_all_warehouse_tables PASSED [ 55%]
tests/test_pipeline.py::test_bad_steward_rows_are_quarantined_with_reasons PASSED [ 57%]
tests/test_pipeline.py::test_duplicates_resolve_to_last_valid_row PASSED [ 59%]
tests/test_pipeline.py::test_unclassified_table_fails_closed PASSED      [ 61%]
tests/test_pipeline.py::test_null_description_is_flagged_not_rejected PASSED [ 63%]
tests/test_pipeline.py::test_rerun_with_same_inputs_is_skipped_and_forced_rerun_changes_nothing PASSED [ 65%]
tests/test_pipeline.py::test_incremental_schema_change_and_new_metadata PASSED [ 67%]
tests/test_pipeline.py::test_missing_required_header_fails_run_and_keeps_catalog PASSED [ 69%]
tests/test_pipeline.py::test_failure_mid_transform_rolls_back_everything PASSED [ 71%]
tests/test_pipeline.py::test_missing_warehouse_is_a_clear_error PASSED   [ 73%]
tests/test_pipeline.py::test_sql_splitter_keeps_semicolons_inside_strings PASSED [ 75%]
tests/test_search.py::test_glossary_bridges_vocabulary_gap PASSED        [ 77%]
tests/test_search.py::test_results_carry_column_evidence PASSED          [ 79%]
tests/test_search.py::test_fts_syntax_and_injection_attempts_are_harmless["] PASSED [ 81%]
tests/test_search.py::test_fts_syntax_and_injection_attempts_are_harmless[AND OR NOT] PASSED [ 83%]
tests/test_search.py::test_fts_syntax_and_injection_attempts_are_harmless[NEAR(] PASSED [ 85%]
tests/test_search.py::test_fts_syntax_and_injection_attempts_are_harmless[* ^ :] PASSED [ 87%]
tests/test_search.py::test_fts_syntax_and_injection_attempts_are_harmless['; DROP TABLE catalog_table; --] PASSED [ 89%]
tests/test_search.py::test_fts_syntax_and_injection_attempts_are_harmless[revenue" OR table_access_role:restricted] PASSED [ 91%]
tests/test_search.py::test_empty_and_oversized_questions_are_rejected PASSED [ 93%]
tests/test_search.py::test_stopword_only_question_returns_nothing PASSED [ 95%]
tests/test_search.py::test_unrelated_question_has_no_match PASSED        [ 97%]
tests/test_search.py::test_term_groups_merge_multiword_synonyms PASSED   [100%]
============================= 49 passed in 5.32s ==============================
```

## 3. Do the tests actually catch bugs? (mutation check)

Each mutation was applied by hand, the suite run, and the code restored.

| Mutation | Result |
|---|---|
| A. Column-level filter in the retrieval SQL disabled | 1 test fails (`test_search_never_returns_unauthorised_tables_or_columns`). The prompt and displayed columns stay clean because a second layer (`v_role_visible_column`) also filters them (defence in depth). |
| B. Table-level filter in the retrieval SQL disabled | 4 tests fail: unauthorised search results, **prompt contents**, eval gate, injection test |
| C. "Answer text mentions table outside evidence" check disabled in the validator | 1 test fails (`test_answer_mentioning_restricted_table_is_rejected`) |

## 4. Evaluation set (29 labelled questions, offline mode, base catalog)

## Summary

| Metric | Value |
|---|---|
| End-to-end pass rate | **28/29 (97%)** |
| Policy leaks (table shown that the role is not granted right now) | **0** |
| Label violations incl. forbidden columns (expected-hidden items shown) | **0** |
| Category `access` | 3/3 |
| Category `column_access` | 2/2 |
| Category `lookup` | 12/13 |
| Category `no_match` | 2/2 |
| Category `synonym` | 9/9 |

## Retrieval setting comparison

Measured on questions with an expected table.

| Setting | hit@1 | hit@3 | MRR |
|---|---|---|---|
| FTS5 BM25 only | 0.909 | 0.909 | 0.909 |
| FTS5 BM25 + glossary expansion (default) | 0.955 | 1.0 | 0.977 |

Per-question results: [eval_report.md](eval_report.md). The single failure (q24, "which customers *bought* which products") is a vocabulary gap in keyword retrieval; it's documented as a limitation rather than patched into the glossary.

## 5. Edge cases demonstrated end-to-end (from `demo_transcript.txt`)

| Scenario | Observed behaviour |
|---|---|
| Rerun with identical inputs | `run 2: SKIPPED  inputs unchanged since last successful run` |
| Steward row with `access_role = superuser` | quarantined; `analytics.daily_sales_summary` becomes `restricted` (admin only) |
| Same question as `analyst` vs `hr_partner` | analyst: `status: no_match` (no hint that HR data exists); HR: `hr.employees` with column evidence |
| Analyst asks for customer e-mails | `sales.customers` shown, `(3 column(s) hidden by your role)` |
| Unanswerable question | `status: low_confidence` with "only part of your question matched" |
| Schema change + batch 002 | `marketing.email_events added`, `support.csat_responses removed`, `access_role (restricted -> public)`, `recipient_email` auto-classified `pii` |
| Unknown role `ceo` | `error: Unknown user role 'ceo'. Known roles: ...` (exit code 1, no model call) |
| Eval after the change batch | eval warns that labels describe the base build; policy leaks still 0 |

## 6. Not verified here

The live-model path (Ollama / Groq) was not run in this environment: no local model or API key was available. Request formats, retries and response parsing are covered by mock-HTTP tests, and answer handling by the scripted-model tests. To check it with a real model:
`LLM_PROVIDER=ollama python -m catalog_assistant eval` (the report shows `ai_validation` per question).
