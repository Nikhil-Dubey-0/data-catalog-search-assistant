"""Catalog build pipeline: harvest -> raw -> staging/validation -> curated -> search index.

Run semantics
-------------
* Every run gets an ``etl_run`` row (running -> success | skipped | failed).
* Inputs are fingerprinted (technical metadata + metadata file bytes). If the
  fingerprint equals the last successful run, the run is recorded as ``skipped``
  and nothing else is written -> reruns are cheap and idempotent.
* All writes happen in one transaction: a failure rolls back raw, curated and
  search changes together, so consumers never see a half-built catalog.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import db
from .config import DEFAULT_METADATA_DIR, PROJECT_ROOT, Settings
from .harvest import TechColumn, TechTable, harvest
from .ingest import discover_files, load_raw

log = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class RunResult:
    run_id: int
    status: str
    fingerprint: str
    stats: dict = field(default_factory=dict)
    error: str | None = None


def compute_fingerprint(tables: list[TechTable], columns: list[TechColumn],
                        files: dict[str, list[Path]]) -> str:
    h = hashlib.sha256()
    # Structure only (not row counts / null fractions): data growth alone is not a catalog change.
    for t in sorted(tables, key=lambda t: (t.schema_name, t.table_name)):
        h.update(f"T|{t.schema_name}|{t.table_name}\n".encode())
    for c in sorted(columns, key=lambda c: (c.schema_name, c.table_name, c.ordinal)):
        h.update(f"C|{c.schema_name}|{c.table_name}|{c.column_name}|{c.data_type}|{c.is_nullable}\n".encode())
    for kind in sorted(files):
        for p in files[kind]:
            h.update(f"F|{kind}|{p.name}\n".encode())
            h.update(p.read_bytes())
    return h.hexdigest()


def _insert_technical(con: sqlite3.Connection, run_id: int, tables: list[TechTable],
                      columns: list[TechColumn]) -> None:
    ts = _now()
    con.executemany(
        "INSERT INTO raw_technical_table (run_id, schema_name, table_name, row_count, harvested_at) "
        "VALUES (?, ?, ?, ?, ?)",
        [(run_id, t.schema_name, t.table_name, t.row_count, ts) for t in tables])
    con.executemany(
        "INSERT INTO raw_technical_column (run_id, schema_name, table_name, column_name, ordinal, data_type, "
        "is_nullable, is_primary_key, null_fraction, distinct_count) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [(run_id,) + tuple(asdict(c).values()) for c in columns])


def collect_stats(con: sqlite3.Connection, run_id: int) -> dict:
    one = lambda sql, *p: con.execute(sql, p).fetchone()[0]  # noqa: E731
    changes = {r["k"]: r["n"] for r in con.execute(
        "SELECT entity_type || '_' || change_type AS k, COUNT(*) AS n FROM change_log "
        "WHERE run_id = ? GROUP BY k", (run_id,))}
    quarantine = {r["entity"]: r["n"] for r in con.execute(
        "SELECT entity, COUNT(*) AS n FROM quarantine WHERE run_id = ? GROUP BY entity", (run_id,))}
    return {
        "active_tables": one("SELECT COUNT(*) FROM catalog_table WHERE is_active = 1"),
        "active_columns": one("SELECT COUNT(*) FROM catalog_column WHERE is_active = 1"),
        "undocumented_tables": one("SELECT COUNT(*) FROM catalog_table WHERE is_active = 1 AND is_documented = 0"),
        "undocumented_columns": one("SELECT COUNT(*) FROM catalog_column WHERE is_active = 1 AND is_documented = 0"),
        "fail_closed_tables": one("SELECT COUNT(*) FROM catalog_table WHERE is_active = 1 "
                                  "AND access_source = 'default_restricted'"),
        "auto_pii_columns": one("SELECT COUNT(*) FROM catalog_column WHERE is_active = 1 "
                                "AND access_source = 'auto_pii_name_rule'"),
        "glossary_terms": one("SELECT COUNT(*) FROM glossary_term"),
        "search_docs": one("SELECT COUNT(*) FROM search_doc"),
        "changes": changes,
        "quarantined": quarantine,
    }


def run_build(settings: Settings, metadata_dirs: list[Path] | None = None,
              force: bool = False) -> RunResult:
    metadata_dirs = metadata_dirs or [DEFAULT_METADATA_DIR]
    con = db.connect(settings.catalog_db)
    try:
        db.init_schema(con)
        run_id = con.execute("INSERT INTO etl_run (started_at, status) VALUES (?, 'running')",
                             (_now(),)).lastrowid
        try:
            files = discover_files(metadata_dirs)
            tables, columns = harvest(settings.warehouse_dir)
            fingerprint = compute_fingerprint(tables, columns, files)
            sources = json.dumps([_display_path(p) for ps in files.values() for p in ps])
            con.execute("UPDATE etl_run SET fingerprint = ?, metadata_sources = ? WHERE run_id = ?",
                        (fingerprint, sources, run_id))

            last = con.execute("SELECT fingerprint FROM etl_run WHERE status = 'success' "
                               "ORDER BY run_id DESC LIMIT 1").fetchone()
            if last and last["fingerprint"] == fingerprint and not force:
                stats = {"reason": "inputs unchanged since last successful run"}
                con.execute("UPDATE etl_run SET status = 'skipped', finished_at = ?, stats_json = ? "
                            "WHERE run_id = ?", (_now(), json.dumps(stats), run_id))
                log.info("Run %s skipped: inputs unchanged", run_id)
                return RunResult(run_id, "skipped", fingerprint, stats)

            with db.transaction(con):
                _insert_technical(con, run_id, tables, columns)
                raw_counts = load_raw(con, run_id, files, PROJECT_ROOT)
                for name in db.TRANSFORM_FILES:
                    log.info("Run %s: executing %s", run_id, name)
                    db.run_sql_file(con, name, {"run_id": run_id})
                stats = collect_stats(con, run_id)
                stats["raw_rows"] = raw_counts
                con.execute("UPDATE etl_run SET status = 'success', finished_at = ?, stats_json = ? "
                            "WHERE run_id = ?", (_now(), json.dumps(stats), run_id))
        except Exception as exc:  # recorded on the run, then re-raised for the caller
            con.execute("UPDATE etl_run SET status = 'failed', finished_at = ?, error = ? WHERE run_id = ?",
                        (_now(), f"{type(exc).__name__}: {exc}"[:2000], run_id))
            log.error("Run %s failed: %s", run_id, exc)
            raise
        log.info("Run %s succeeded: %s", run_id, stats["changes"])
        return RunResult(run_id, "success", fingerprint, stats)
    finally:
        con.close()


def _display_path(p: Path) -> str:
    try:
        return p.resolve().relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return str(p)
