"""Harvest technical metadata (tables, columns, types, simple profiles) from the warehouse."""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


class WarehouseNotFoundError(RuntimeError):
    pass


@dataclass(frozen=True)
class TechTable:
    schema_name: str
    table_name: str
    row_count: int


@dataclass(frozen=True)
class TechColumn:
    schema_name: str
    table_name: str
    column_name: str
    ordinal: int
    data_type: str
    is_nullable: int
    is_primary_key: int
    null_fraction: float | None
    distinct_count: int


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def harvest(warehouse_dir: Path) -> tuple[list[TechTable], list[TechColumn]]:
    """Read every <schema>.db file in read-only mode and profile its tables."""
    files = sorted(warehouse_dir.glob("*.db")) if warehouse_dir.exists() else []
    if not files:
        raise WarehouseNotFoundError(
            f"No warehouse databases found in {warehouse_dir}. "
            "Run `python -m catalog_assistant init-warehouse` first.")
    tables: list[TechTable] = []
    columns: list[TechColumn] = []
    for db in files:
        schema = db.stem.lower()
        con = sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True)
        try:
            names = [r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
            for table in names:
                qt = _quote(table)
                row_count = con.execute(f"SELECT COUNT(*) FROM {qt}").fetchone()[0]
                tables.append(TechTable(schema, table.lower(), row_count))
                for cid, col, ctype, notnull, _default, pk in con.execute(f"PRAGMA table_info({qt})"):
                    qc = _quote(col)
                    nulls, distinct = con.execute(
                        f"SELECT SUM(CASE WHEN {qc} IS NULL THEN 1 ELSE 0 END), COUNT(DISTINCT {qc}) FROM {qt}"
                    ).fetchone()
                    columns.append(TechColumn(
                        schema_name=schema, table_name=table.lower(), column_name=col.lower(),
                        ordinal=cid + 1, data_type=(ctype or "ANY").upper(),
                        is_nullable=0 if (notnull or pk) else 1, is_primary_key=1 if pk else 0,
                        null_fraction=round((nulls or 0) / row_count, 4) if row_count else None,
                        distinct_count=distinct,
                    ))
        finally:
            con.close()
    log.info("Harvested %d tables / %d columns from %d schemas", len(tables), len(columns), len(files))
    return tables, columns
