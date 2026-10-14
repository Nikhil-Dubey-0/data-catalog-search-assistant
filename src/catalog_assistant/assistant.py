"""The search assistant: retrieval first, AI second, validation always.

    question + role
        -> (AI, optional) rewrite into extra search keywords      [validated, can only add search terms]
        -> role-filtered retrieval with evidence                    [SQL; the access boundary]
        -> no evidence?  -> "no matching dataset you can access"   [AI is not called]
        -> (AI, optional) choose datasets + explain from evidence  [validated against evidence]
        -> AI missing / failed / rejected -> deterministic answer   [always available]
        -> logged to search_log
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from . import prompts
from .llm import LLMClient, LLMError
from .search import (MAX_QUESTION_CHARS, InvalidQueryError, TableResult, all_table_keys,
                     check_role, search, visible_columns)
from .validation import validate_answer, validate_keywords

log = logging.getLogger(__name__)

CONF_RANK = {"low": 0, "medium": 1, "high": 2}
MAX_EVIDENCE_COLUMNS = 30


@dataclass
class Recommendation:
    table_key: str
    description: str | None
    owner: str | None
    access_role: str
    certified: bool
    confidence: str
    matched_terms: list[str]
    columns: list[dict]
    reason: str
    source: str  # ai | retrieval
    hidden_column_count: int = 0


@dataclass
class AssistantAnswer:
    question: str
    user_role: str
    status: str            # answered | low_confidence | insufficient_evidence | no_match
    answer: str
    confidence: str
    recommendations: list[Recommendation]
    provider: str
    ai_validation: str     # passed | rejected: ... | error: ... | not_called: ...
    notes: list[str] = field(default_factory=list)
    search_terms: list[str] = field(default_factory=list)
    candidates: list[TableResult] = field(default_factory=list)
    latency_ms: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


class CatalogAssistant:
    def __init__(self, con: sqlite3.Connection, llm: LLMClient | None = None, top_k: int = 5,
                 use_query_rewrite: bool = True, log_queries: bool = True):
        self.con = con
        self.llm = llm
        self.top_k = top_k
        self.use_query_rewrite = use_query_rewrite
        self.log_queries = log_queries

    @property
    def provider(self) -> str:
        return self.llm.name if self.llm else "offline"

    # ------------------------------------------------------------------ ask --
    def ask(self, question: str, user_role: str) -> AssistantAnswer:
        t0 = time.perf_counter()
        question = (question or "").strip()
        if not question:
            raise InvalidQueryError("Question is empty.")
        if len(question) > MAX_QUESTION_CHARS:
            raise InvalidQueryError(f"Question is longer than {MAX_QUESTION_CHARS} characters.")
        check_role(self.con, user_role)  # fail before spending any model call

        notes: list[str] = []
        ai_down = False
        keywords: list[str] = []
        if self.llm and self.use_query_rewrite:
            try:
                raw = self.llm.generate_json(prompts.REWRITE_SYSTEM, prompts.rewrite_user_prompt(question),
                                             prompts.REWRITE_SCHEMA)
                keywords, problems = validate_keywords(raw)
                notes.extend(problems)
            except LLMError as exc:
                ai_down = True
                notes.append(f"AI unavailable, using deterministic search and answer ({exc})")

        sr = search(self.con, question, user_role, self.top_k, extra_keywords=keywords)
        terms = [f"{g.label} ({g.source})" for g in sr.groups]

        if not sr.results:
            ans = AssistantAnswer(
                question, user_role, "no_match",
                "I could not find a dataset you have access to that matches this question. "
                "Try different business terms, or contact the data platform team if you expect "
                "restricted data to exist.",
                "low", [], self.provider, "not_called: no evidence retrieved", notes, terms, [])
            return self._finish(ans, t0)

        evidence, evidence_cols = self._evidence(sr.results, user_role)
        ans: AssistantAnswer | None = None
        ai_status = ""
        if self.llm and not ai_down:
            try:
                raw = self.llm.generate_json(prompts.ANSWER_SYSTEM,
                                             prompts.answer_user_prompt(question, user_role, evidence),
                                             prompts.ANSWER_SCHEMA)
                v = validate_answer(raw, evidence_cols, all_table_keys(self.con))
                if v.ok:
                    notes.extend(v.warnings)
                    ans = self._from_ai(question, user_role, v, sr.results, notes, terms)
                else:
                    notes.append("AI answer rejected by validation: " + "; ".join(v.errors))
                    ai_status = "rejected: " + "; ".join(v.errors)
            except LLMError as exc:
                notes.append(f"AI unavailable, deterministic answer used ({exc})")
                ai_status = f"error: {exc}"
        else:
            ai_status = "not_called: offline mode" if not self.llm else "not_called: AI unavailable"

        if ans is None:
            ans = self._deterministic(question, user_role, sr.results, notes, terms, ai_status)
        return self._finish(ans, t0)

    # -------------------------------------------------------------- helpers --
    def _evidence(self, results: list[TableResult], user_role: str):
        evidence, cols_by_table = [], {}
        for r in results:
            cols = visible_columns(self.con, r.table_key, user_role)
            cols_by_table[r.table_key] = {c["column_name"] for c in cols}
            evidence.append({
                "table_key": r.table_key,
                "description": r.description or "(no description)",
                "owner": r.owner,
                "certified": r.certified,
                "refresh_frequency": r.refresh_frequency,
                "matched_terms": r.matched_terms,
                "retrieval_confidence": r.confidence,
                "columns": [{"name": c["column_name"], "type": c["data_type"],
                             "description": (c["description"] or "")[:160]}
                            for c in cols[:MAX_EVIDENCE_COLUMNS]],
            })
        return evidence, cols_by_table

    def _columns_for(self, table_key: str, user_role: str, names: list[str]) -> list[dict]:
        visible = {c["column_name"]: c for c in visible_columns(self.con, table_key, user_role)}
        return [{"name": n, "type": visible[n]["data_type"], "description": visible[n]["description"]}
                for n in names if n in visible]

    def _rec(self, r: TableResult, user_role: str, cols: list[str], reason: str, source: str) -> Recommendation:
        return Recommendation(r.table_key, r.description, r.owner, r.access_role, r.certified, r.confidence,
                              r.matched_terms, self._columns_for(r.table_key, user_role, cols), reason, source,
                              r.hidden_column_count)

    def _from_ai(self, question, user_role, v, results, notes, terms) -> AssistantAnswer:
        by_key = {r.table_key: r for r in results}
        recs = [self._rec(by_key[x.table], user_role,
                          x.columns or [c.column_name for c in by_key[x.table].matched_columns],
                          x.reason or "selected by AI from the retrieved evidence", "ai")
                for x in v.recommendations]
        if not recs:
            return AssistantAnswer(question, user_role, "insufficient_evidence", v.answer or
                                   "The retrieved datasets do not appear to answer this question.",
                                   "low", [], self.provider, "passed", notes, terms, results)
        # The model may lower confidence, never raise it above what retrieval supports.
        conf = min(v.confidence, recs[0].confidence, key=lambda c: CONF_RANK[c])
        status = "low_confidence" if conf == "low" else "answered"
        return AssistantAnswer(question, user_role, status, v.answer, conf, recs, self.provider,
                               "passed", notes, terms, results)

    def _deterministic(self, question, user_role, results, notes, terms, ai_status) -> AssistantAnswer:
        top = results[0]
        keep = [r for r in results[:3] if r is top or r.coverage >= 0.5 * top.coverage]
        recs = [self._rec(r, user_role, [c.column_name for c in r.matched_columns],
                          f"matched terms: {', '.join(r.matched_terms)}", "retrieval") for r in keep]
        cols = ", ".join(c["name"] for c in recs[0].columns) or "none matched directly"
        desc = top.description or "no description yet (matched on table/column names only)"
        text = f"Best match: {top.table_key} - {desc} Relevant columns: {cols}."
        if top.owner:
            text += f" Owner: {top.owner}."
        if len(recs) > 1:
            text += " Also relevant: " + ", ".join(r.table_key for r in recs[1:]) + "."
        status = "answered"
        if top.confidence == "low":
            status = "low_confidence"
            text = "Low confidence - only part of your question matched. " + text
        return AssistantAnswer(question, user_role, status, text, top.confidence, recs, self.provider,
                               ai_status, notes, terms, results)

    def _finish(self, ans: AssistantAnswer, t0: float) -> AssistantAnswer:
        ans.latency_ms = int((time.perf_counter() - t0) * 1000)
        if self.log_queries:
            self.con.execute(
                "INSERT INTO search_log (asked_at, user_role, question, status, provider, ai_validation, "
                "top_tables, latency_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (datetime.now(timezone.utc).isoformat(timespec="seconds"), ans.user_role, ans.question,
                 ans.status, ans.provider, ans.ai_validation,
                 json.dumps([r.table_key for r in ans.recommendations]), ans.latency_ms))
        log.info("ask role=%s status=%s ai=%s", ans.user_role, ans.status, ans.ai_validation)
        return ans
