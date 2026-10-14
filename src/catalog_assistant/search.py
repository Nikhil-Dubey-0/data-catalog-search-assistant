"""Role-aware retrieval over the curated catalog.

1. The question is tokenised; glossary phrases (e.g. "attrition" -> churn) become
   *term groups* with their synonyms; leftover meaningful words are their own groups.
2. One FTS5 query ranks table and column documents with BM25. The access-role filter
   is part of that same SQL query, so unauthorised metadata is never retrieved and
   therefore can never reach the AI layer or the output.
3. Documents are aggregated per table; each result carries its evidence: matched
   terms, matched columns (with type/description) and a coverage-based confidence.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import asdict, dataclass, field

MAX_QUESTION_CHARS = 500
MAX_GROUPS = 12

STOPWORDS = frozenset("""
a an the and or of for to in on at by with from about into over per vs via is are was were be been
i me my we our you your it its this that these those there here which what where who whom whose when how
can could would should do does did have has had need needs want wants looking look find finding show
give get list see tell know any some all each every more most many much table tables dataset datasets data
column columns field fields information info source sources please help where's whats which's
number numbers figure figures stat stats statistics record records detail details
they them their theirs he she him her his us its
""".split())


class UnknownRoleError(ValueError):
    pass


class InvalidQueryError(ValueError):
    pass


@dataclass
class TermGroup:
    label: str
    variants: list[str]
    source: str  # query | glossary | ai


@dataclass
class ColumnEvidence:
    column_name: str
    data_type: str | None
    description: str | None
    is_pii: bool
    score: float = 0.0


@dataclass
class TableResult:
    table_key: str
    description: str | None
    owner: str | None
    domain: str | None
    tags: str | None
    access_role: str
    certified: bool
    refresh_frequency: str | None
    row_count: int | None
    is_documented: bool
    score: float
    coverage: float
    confidence: str
    matched_terms: list[str]
    matched_columns: list[ColumnEvidence]
    table_doc_matched: bool
    glossary_terms: list[str] = field(default_factory=list)
    hidden_column_count: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SearchResponse:
    question: str
    user_role: str
    groups: list[TermGroup]
    results: list[TableResult]


# ----------------------------------------------------------------- helpers ---
def tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _singular(word: str) -> str:
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _clean_phrase(phrase: str) -> str:
    return " ".join(tokenize(phrase))


def check_role(con: sqlite3.Connection, user_role: str) -> None:
    if not con.execute("SELECT 1 FROM user_role WHERE user_role = ?", (user_role,)).fetchone():
        known = [r[0] for r in con.execute("SELECT user_role FROM user_role ORDER BY 1")]
        raise UnknownRoleError(f"Unknown user role '{user_role}'. Known roles: {', '.join(known)}")


def list_roles(con: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in con.execute(
        "SELECT u.user_role, u.description, GROUP_CONCAT(g.access_role, ', ') AS grants "
        "FROM user_role u LEFT JOIN role_grant g ON g.user_role = u.user_role "
        "GROUP BY u.user_role ORDER BY u.user_role")]


def load_glossary(con: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in con.execute("SELECT term, definition, synonyms, related_tables FROM glossary_term")]


def build_term_groups(question: str, glossary: list[dict], extra_keywords: list[str] | None = None,
                      use_glossary: bool = True) -> list[TermGroup]:
    tokens = tokenize(question)
    singular = [_singular(t) for t in tokens]
    covered: set[int] = set()
    groups: list[TermGroup] = []

    if use_glossary:
        for entry in glossary:
            variants = [_clean_phrase(entry["term"])] + [
                _clean_phrase(s) for s in (entry["synonyms"] or "").split(";") if _clean_phrase(s)]
            hit = False
            for phrase in variants:
                p = [_singular(w) for w in phrase.split()]
                for i in range(len(tokens) - len(p) + 1):
                    if singular[i:i + len(p)] == p:
                        covered.update(range(i, i + len(p)))
                        hit = True
            if hit:
                groups.append(TermGroup(entry["term"], list(dict.fromkeys(variants)), "glossary"))

    seen = {v for g in groups for v in g.variants}
    for i, tok in enumerate(tokens):
        if i in covered or tok in STOPWORDS or len(tok) < 2 or tok in seen:
            continue
        seen.add(tok)
        groups.append(TermGroup(tok, [tok], "query"))

    for kw in extra_keywords or []:
        kw = _clean_phrase(kw)
        if kw and kw not in seen and kw not in STOPWORDS:
            seen.add(kw)
            groups.append(TermGroup(kw, [kw], "ai"))
    return groups[:MAX_GROUPS]


def fts_expression(groups: list[TermGroup]) -> str:
    """OR of quoted phrases. Input is already reduced to [a-z0-9 ], so no FTS syntax can leak in."""
    variants = [v for g in groups for v in g.variants]
    return " OR ".join('"' + v.replace('"', '""') + '"' for v in dict.fromkeys(variants))


_ACCESS_FILTER = """
    table_access_role IN (SELECT access_role FROM role_grant WHERE user_role = :role)
    AND (column_access_role IS NULL
         OR column_access_role IN (SELECT access_role FROM role_grant WHERE user_role = :role))
"""


def _confidence(coverage: float) -> str:
    if coverage >= 0.67:
        return "high"
    if coverage >= 0.34:
        return "medium"
    return "low"


# ------------------------------------------------------------------ search ---
def search(con: sqlite3.Connection, question: str, user_role: str, limit: int = 5,
           extra_keywords: list[str] | None = None, use_glossary: bool = True) -> SearchResponse:
    question = (question or "").strip()
    if not question:
        raise InvalidQueryError("Question is empty.")
    if len(question) > MAX_QUESTION_CHARS:
        raise InvalidQueryError(f"Question is longer than {MAX_QUESTION_CHARS} characters.")
    check_role(con, user_role)

    glossary = load_glossary(con)
    groups = build_term_groups(question, glossary, extra_keywords, use_glossary)
    if not groups:
        return SearchResponse(question, user_role, groups, [])

    # name (table/column words) weighs most, then description, then context
    rows = con.execute(f"""
        SELECT doc_type, table_key, column_name,
               -bm25(search_doc, 0, 0, 0, 0, 0, 4.0, 1.5, 0.5) AS s
        FROM search_doc
        WHERE search_doc MATCH :q AND {_ACCESS_FILTER}
        """, {"q": fts_expression(groups), "role": user_role}).fetchall()

    per_table: dict[str, dict] = {}
    for r in rows:
        t = per_table.setdefault(r["table_key"], {"table_s": 0.0, "cols": {}})
        if r["doc_type"] == "table":
            t["table_s"] = max(t["table_s"], r["s"])
        else:
            t["cols"][r["column_name"]] = r["s"]
    if not per_table:
        return SearchResponse(question, user_role, groups, [])

    # Which term groups does each candidate table match? (same tokenizer + same access filter)
    keys = list(per_table)
    key_params = {f"k{i}": k for i, k in enumerate(keys)}
    in_list = ",".join(f":{p}" for p in key_params)
    matched: dict[str, list[TermGroup]] = {k: [] for k in keys}
    for g in groups:
        hits = con.execute(
            f"SELECT DISTINCT table_key FROM search_doc WHERE search_doc MATCH :q "
            f"AND table_key IN ({in_list}) AND {_ACCESS_FILTER}",
            {"q": fts_expression([g]), "role": user_role, **key_params}).fetchall()
        for h in hits:
            matched[h["table_key"]].append(g)

    related: dict[str, list[str]] = {}
    matched_glossary = {g.label for g in groups if g.source == "glossary"}
    for entry in glossary:
        if entry["term"] in matched_glossary:
            for tk in (entry["related_tables"] or "").split(";"):
                related.setdefault(tk.strip(), []).append(entry["term"])

    base_groups = [g for g in groups if g.source != "ai"]
    ai_groups = [g for g in groups if g.source == "ai"]
    scored = []
    for tk, t in per_table.items():
        cols = sorted(t["cols"].items(), key=lambda kv: kv[1], reverse=True)
        raw = t["table_s"] + 0.5 * sum(s for _, s in cols[:3])
        mg = matched[tk]
        cov = (sum(1 for g in mg if g.source != "ai") / len(base_groups)) if base_groups else 0.0
        if ai_groups:
            cov = max(cov, sum(1 for g in mg if g.source == "ai") / len(ai_groups))
        boost = 1.3 if tk in related else 1.0
        scored.append((raw * boost * (0.5 + cov), cov, tk, cols, mg))
    scored.sort(key=lambda x: x[0], reverse=True)

    results = [
        _build_result(con, tk, user_role, score, cov, cols, mg, related.get(tk, []),
                      per_table[tk]["table_s"] > 0)
        for score, cov, tk, cols, mg in scored[:limit]
    ]
    return SearchResponse(question, user_role, groups, results)


def _build_result(con, table_key, user_role, score, coverage, cols, groups, glossary_terms,
                  table_doc_matched) -> TableResult:
    t = con.execute("SELECT * FROM catalog_table WHERE table_key = ?", (table_key,)).fetchone()
    col_meta = {r["column_name"]: r for r in con.execute(
        "SELECT column_name, data_type, description, is_pii FROM catalog_column "
        "WHERE table_key = ? AND is_active = 1", (table_key,))}
    visible = con.execute("SELECT COUNT(*) FROM v_role_visible_column WHERE user_role = ? AND table_key = ?",
                          (user_role, table_key)).fetchone()[0]
    evidence = [ColumnEvidence(name, col_meta[name]["data_type"], col_meta[name]["description"],
                               bool(col_meta[name]["is_pii"]), round(s, 3))
                for name, s in cols[:5] if name in col_meta]
    return TableResult(
        table_key=table_key, description=t["description"], owner=t["owner"], domain=t["domain"],
        tags=t["tags"], access_role=t["access_role"], certified=bool(t["certified"]),
        refresh_frequency=t["refresh_frequency"], row_count=t["row_count"],
        is_documented=bool(t["is_documented"]), score=round(score, 3), coverage=round(coverage, 2),
        confidence=_confidence(coverage), matched_terms=[g.label for g in groups],
        matched_columns=evidence, table_doc_matched=table_doc_matched,
        glossary_terms=glossary_terms, hidden_column_count=len(col_meta) - visible,
    )


def visible_columns(con: sqlite3.Connection, table_key: str, user_role: str) -> list[dict]:
    return [dict(r) for r in con.execute(
        "SELECT column_name, data_type, description, is_pii FROM v_role_visible_column "
        "WHERE user_role = ? AND table_key = ? ORDER BY ordinal", (user_role, table_key))]


def visible_tables(con: sqlite3.Connection, user_role: str) -> list[dict]:
    check_role(con, user_role)
    return [dict(r) for r in con.execute(
        "SELECT table_key, description, owner, access_role, certified, row_count, column_count, is_documented "
        "FROM v_role_visible_table WHERE user_role = ? ORDER BY table_key", (user_role,))]


def all_table_keys(con: sqlite3.Connection) -> set[str]:
    return {r[0] for r in con.execute("SELECT table_key FROM catalog_table")}
