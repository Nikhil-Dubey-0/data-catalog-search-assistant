"""Quality gate on the labelled evaluation set (offline mode, deterministic)."""

from __future__ import annotations

from catalog_assistant.config import EVAL_FILE
from catalog_assistant.evaluate import load_cases, run_eval


def test_eval_set_has_zero_access_leaks_and_high_pass_rate(con):
    report = run_eval(con, load_cases(EVAL_FILE))
    assert report["access_leaks"] == 0
    assert report["pass_rate"] >= 0.9
    # the glossary design decision must keep paying for itself
    assert report["retrieval_fts_glossary"]["hit@1"] >= report["retrieval_fts_only"]["hit@1"]
