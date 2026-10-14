"""Evaluation harness: retrieval quality, answer correctness and access-control leakage.

Each question in data/eval/eval_questions.csv has a role and either expected tables
(any-of), forbidden tables/columns that must never be shown to that role, or the
category ``no_match`` (the assistant must not answer confidently).

Two retrieval settings are compared to justify the glossary design decision:
FTS/BM25 only vs FTS/BM25 + glossary synonym expansion.
"""

from __future__ import annotations

import csv
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .assistant import CatalogAssistant
from .llm import LLMClient
from .search import search, visible_columns


@dataclass
class EvalCase:
    id: str
    question: str
    role: str
    expected: list[str]
    forbidden_tables: list[str]
    forbidden_columns: list[str]
    category: str


@dataclass
class CaseResult:
    case: EvalCase
    status: str
    top_answer: str | None
    ranked: list[str]
    rank: int | None
    passed: bool
    leaks: list[str] = field(default_factory=list)
    notes: str = ""


def _split(v: str | None) -> list[str]:
    return [x.strip() for x in (v or "").split(";") if x.strip()]


def load_cases(path: Path) -> list[EvalCase]:
    with path.open(newline="", encoding="utf-8-sig") as fh:
        return [EvalCase(r["id"], r["question"], r["role"], _split(r["expected_tables"]),
                         _split(r["forbidden_tables"]), _split(r["forbidden_columns"]), r["category"])
                for r in csv.DictReader(fh)]


def _authorized(con: sqlite3.Connection, role: str, table_key: str) -> bool:
    return con.execute("SELECT 1 FROM v_role_visible_table WHERE user_role = ? AND table_key = ?",
                       (role, table_key)).fetchone() is not None


def retrieval_metrics(con: sqlite3.Connection, cases: list[EvalCase], use_glossary: bool) -> dict:
    scored = [c for c in cases if c.expected]
    hit1 = hit3 = 0
    rr = 0.0
    for c in scored:
        ranked = [r.table_key for r in search(con, c.question, c.role, 5, use_glossary=use_glossary).results]
        rank = next((i + 1 for i, t in enumerate(ranked) if t in c.expected), None)
        hit1 += rank == 1
        hit3 += rank is not None and rank <= 3
        rr += 1 / rank if rank else 0
    n = len(scored) or 1
    return {"questions": len(scored), "hit@1": round(hit1 / n, 3), "hit@3": round(hit3 / n, 3),
            "mrr": round(rr / n, 3)}


def run_eval(con: sqlite3.Connection, cases: list[EvalCase], llm: LLMClient | None = None) -> dict:
    assistant = CatalogAssistant(con, llm, log_queries=False)
    results: list[CaseResult] = []
    for c in cases:
        ans = assistant.ask(c.question, c.role)
        ranked = [r.table_key for r in ans.candidates]
        shown = {r.table_key for r in ans.recommendations} | set(ranked)
        # Everything the user saw or the model was sent: candidates, their visible columns, recommendations.
        cols_shown = set()
        for tk in shown:
            cols_shown |= {f"{tk}.{x['column_name']}" for x in visible_columns(con, tk, c.role)}
        for r in ans.recommendations:
            cols_shown |= {f"{r.table_key}.{x['name']}" for x in r.columns}

        # unauthorised = violates the policy in the catalog right now (a real leak);
        # forbidden = violates the labels in the eval file (written for the base build)
        leaks = [f"unauthorised:{t}" for t in shown if not _authorized(con, c.role, t)]
        leaks += [f"forbidden:{t}" for t in shown if t in c.forbidden_tables]
        leaks += [f"forbidden:{col}" for col in c.forbidden_columns if col in cols_shown]
        top = ans.recommendations[0].table_key if ans.recommendations else None
        rank = next((i + 1 for i, t in enumerate(ranked) if t in c.expected), None)

        if c.category == "no_match":
            passed = ans.status in ("no_match", "low_confidence", "insufficient_evidence")
        elif c.expected:
            passed = top in c.expected and ans.status == "answered"
        else:  # access / column_access cases: correct iff nothing forbidden is exposed
            passed = True
        passed = passed and not leaks
        results.append(CaseResult(c, ans.status, top, ranked, rank, passed, leaks, ans.ai_validation))

    by_cat: dict[str, list[CaseResult]] = {}
    for r in results:
        by_cat.setdefault(r.case.category, []).append(r)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "provider": assistant.provider,
        "cases": results,
        "pass_rate": round(sum(r.passed for r in results) / len(results), 3),
        "passed": sum(r.passed for r in results),
        "total": len(results),
        "access_leaks": sum(len(r.leaks) for r in results),
        "policy_leaks": sum(1 for r in results for x in r.leaks if x.startswith("unauthorised:")),
        "catalog_state_note": _state_note(con),
        "by_category": {k: f"{sum(r.passed for r in v)}/{len(v)}" for k, v in sorted(by_cat.items())},
        "retrieval_fts_only": retrieval_metrics(con, cases, use_glossary=False),
        "retrieval_fts_glossary": retrieval_metrics(con, cases, use_glossary=True),
    }


def _state_note(con: sqlite3.Connection) -> str | None:
    row = con.execute("SELECT metadata_sources FROM etl_run WHERE status = 'success' "
                      "ORDER BY run_id DESC LIMIT 1").fetchone()
    if row and row[0] and "changes/" in row[0]:
        return ("Catalog includes change batches; the eval labels describe the base build, so "
                "'forbidden' findings may reflect intended policy changes. Rebuild with "
                "`python -m catalog_assistant setup --force` for a like-for-like run.")
    return None


def render_markdown(report: dict) -> str:
    a, b = report["retrieval_fts_only"], report["retrieval_fts_glossary"]
    lines = [
        "# Evaluation report",
        "",
        f"Generated {report['generated_at']} with provider `{report['provider']}` against the current catalog build.",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| End-to-end pass rate | **{report['passed']}/{report['total']} ({report['pass_rate']:.0%})** |",
        f"| Policy leaks (table shown that the role is not granted right now) | **{report['policy_leaks']}** |",
        f"| Label violations incl. forbidden columns (expected-hidden items shown) | **{report['access_leaks']}** |",
    ]
    if report.get("catalog_state_note"):
        lines[4:4] = [f"> **Note:** {report['catalog_state_note']}", ""]
    lines += [f"| Category `{k}` | {v} |" for k, v in report["by_category"].items()]
    lines += [
        "",
        "## Retrieval setting comparison",
        "",
        "Measured on questions with an expected table.",
        "",
        "| Setting | hit@1 | hit@3 | MRR |",
        "|---|---|---|---|",
        f"| FTS5 BM25 only | {a['hit@1']} | {a['hit@3']} | {a['mrr']} |",
        f"| FTS5 BM25 + glossary expansion (default) | {b['hit@1']} | {b['hit@3']} | {b['mrr']} |",
        "",
        "## Per question",
        "",
        "| id | role | category | question | expected | top answer | status | rank | AI validation | pass |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in report["cases"]:
        c = r.case
        exp = ", ".join(c.expected) or ("none (must not leak: " + ", ".join(c.forbidden_tables + c.forbidden_columns)
                                        + ")" if c.forbidden_tables or c.forbidden_columns else "none")
        lines.append(f"| {c.id} | {c.role} | {c.category} | {c.question} | {exp} | {r.top_answer or '-'} | "
                     f"{r.status} | {r.rank or '-'} | {r.notes.split(':')[0]} | {'PASS' if r.passed else 'FAIL'}"
                     + (f" (leak: {', '.join(r.leaks)})" if r.leaks else "") + " |")
    return "\n".join(lines) + "\n"
