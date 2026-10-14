"""SQLite helpers: connections, running .sql files with parameters, transactions."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .config import SQL_DIR

SCHEMA_FILES = ("01_catalog_schema.sql", "02_views.sql")
TRANSFORM_FILES = (
    "10_stage_business_metadata.sql",
    "20_merge_catalog.sql",
    "30_refresh_search_index.sql",
)


def connect(path: Path) -> sqlite3.Connection:
    """Open the catalog DB in autocommit mode; callers manage transactions explicitly."""
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, isolation_level=None, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def iter_statements(sql_text: str) -> Iterator[str]:
    """Split a script into complete statements (handles ';' inside strings)."""
    buf: list[str] = []
    for line in sql_text.splitlines(keepends=True):
        buf.append(line)
        stmt = "".join(buf)
        if sqlite3.complete_statement(stmt):
            if stmt.strip():
                yield stmt
            buf = []
    rest = "".join(buf).strip()
    # Trailing comment-only text is fine; anything else is an unterminated statement.
    if rest and any(not ln.strip().startswith("--") for ln in rest.splitlines() if ln.strip()):
        raise ValueError(f"Unterminated SQL statement: {rest[:80]}...")


def run_sql_file(con: sqlite3.Connection, name: str, params: dict | None = None) -> None:
    """Execute every statement in sql/<name>, binding named parameters such as :run_id."""
    text = (SQL_DIR / name).read_text(encoding="utf-8")
    for stmt in iter_statements(text):
        try:
            con.execute(stmt, params or {})
        except sqlite3.Error as exc:
            raise sqlite3.Error(f"{name}: {exc}\n--- statement ---\n{stmt.strip()[:400]}") from exc


def init_schema(con: sqlite3.Connection) -> None:
    for name in SCHEMA_FILES:
        run_sql_file(con, name)


@contextmanager
def transaction(con: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """All-or-nothing unit of work: a failed run leaves the curated catalog untouched."""
    con.execute("BEGIN IMMEDIATE")
    try:
        yield con
    except BaseException:
        con.execute("ROLLBACK")
        raise
    else:
        con.execute("COMMIT")
