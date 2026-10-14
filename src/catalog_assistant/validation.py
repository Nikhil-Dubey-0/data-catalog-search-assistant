"""Validate model output against the evidence that was actually retrieved.

The model is treated as an untrusted component: its JSON is parsed defensively and
every table/column it names must exist in the role-filtered evidence. Answers that
mention a table outside the evidence are rejected as a whole (that would be either a
hallucination or a leak); unknown columns are dropped with a note.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

TABLE_REF = re.compile(r"\b([a-z][a-z0-9_]*\.[a-z][a-z0-9_]*)\b")
VALID_CONFIDENCE = {"high", "medium", "low"}
KEYWORD_RE = re.compile(r"^[a-z0-9][a-z0-9 _-]{0,39}$")


@dataclass
class ValidatedRecommendation:
    table: str
    columns: list[str]
    reason: str


@dataclass
class AnswerValidation:
    ok: bool
    errors: list[str] = field(default_factory=list)     # fatal: AI answer rejected
    warnings: list[str] = field(default_factory=list)   # repaired: answer kept
    answer: str = ""
    recommendations: list[ValidatedRecommendation] = field(default_factory=list)
    confidence: str = "low"
    insufficient_evidence: bool = False


def extract_json_object(raw: str) -> dict:
    """Parse a JSON object, tolerating code fences or chatter around it."""
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("empty response")
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text)
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("no JSON object found in response") from None
        try:
            obj = json.loads(text[start:end + 1])
        except json.JSONDecodeError as exc:
            raise ValueError(f"malformed JSON: {exc.msg}") from None
    if not isinstance(obj, dict):
        raise ValueError("response JSON is not an object")
    return obj


def validate_keywords(raw: str, max_keywords: int = 8) -> tuple[list[str], list[str]]:
    """Return (clean keywords, problems). Bad keywords are dropped, never passed on."""
    problems: list[str] = []
    try:
        obj = extract_json_object(raw)
    except ValueError as exc:
        return [], [f"query rewrite ignored: {exc}"]
    kws = obj.get("keywords")
    if not isinstance(kws, list):
        return [], ["query rewrite ignored: 'keywords' is not a list"]
    clean: list[str] = []
    for kw in kws:
        if isinstance(kw, str) and KEYWORD_RE.match(kw.strip().lower()):
            clean.append(kw.strip().lower())
        else:
            problems.append(f"dropped invalid keyword {str(kw)[:40]!r}")
    return list(dict.fromkeys(clean))[:max_keywords], problems


def validate_answer(raw: str, evidence_columns: dict[str, set[str]], known_tables: set[str]) -> AnswerValidation:
    """``evidence_columns`` maps each allowed table_key to the column names the user may see."""
    v = AnswerValidation(ok=False)
    try:
        obj = extract_json_object(raw)
    except ValueError as exc:
        v.errors.append(str(exc))
        return v

    answer = obj.get("answer")
    recs = obj.get("recommendations")
    confidence = obj.get("confidence")
    insufficient = obj.get("insufficient_evidence")
    if not isinstance(answer, str) or not answer.strip():
        v.errors.append("missing or empty 'answer'")
    if not isinstance(recs, list):
        v.errors.append("'recommendations' is not a list")
        recs = []
    if confidence not in VALID_CONFIDENCE:
        v.warnings.append(f"invalid confidence {confidence!r}; treated as 'low'")
        confidence = "low"
    if not isinstance(insufficient, bool):
        v.warnings.append("missing 'insufficient_evidence'; inferred from recommendations")
        insufficient = len(recs) == 0

    seen: set[str] = set()
    for i, rec in enumerate(recs[:3]):
        if not isinstance(rec, dict):
            v.errors.append(f"recommendation {i} is not an object")
            continue
        table = str(rec.get("table", "")).strip().lower()
        if table not in evidence_columns:
            kind = "restricted or unretrieved" if table in known_tables else "non-existent"
            v.errors.append(f"recommendation cites {kind} table {table!r} that is not in the evidence")
            continue
        if table in seen:
            v.warnings.append(f"duplicate recommendation for {table} removed")
            continue
        seen.add(table)
        cols_in = rec.get("columns") if isinstance(rec.get("columns"), list) else []
        cols = []
        for c in cols_in:
            name = str(c).strip().lower()
            if name in evidence_columns[table]:
                cols.append(name)
            else:
                v.warnings.append(f"dropped column {name!r}: not a visible column of {table}")
        reason = str(rec.get("reason", "")).strip()
        if not reason:
            v.warnings.append(f"recommendation for {table} has no reason")
        v.recommendations.append(ValidatedRecommendation(table, list(dict.fromkeys(cols)), reason))

    if isinstance(answer, str):
        for ref in set(TABLE_REF.findall(answer.lower())):
            if ref in known_tables and ref not in evidence_columns:
                v.errors.append(f"answer text mentions table {ref!r} outside the evidence")
    if insufficient and v.recommendations:
        v.warnings.append("model flagged insufficient evidence but still recommended tables; recommendations kept")
        insufficient = False

    v.answer = (answer or "").strip() if isinstance(answer, str) else ""
    v.confidence = confidence
    v.insufficient_evidence = bool(insufficient)
    v.ok = not v.errors
    return v
