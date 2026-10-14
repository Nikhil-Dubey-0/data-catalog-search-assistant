"""Load steward metadata CSV files into the raw layer, exactly as received.

Only the *file-level* contract is enforced here (file present, required headers);
row-level validation happens in SQL staging (sql/10_stage_business_metadata.sql)
so that bad rows are quarantined with a reason instead of crashing the run.
"""

from __future__ import annotations

import csv
import logging
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)


class MetadataSchemaError(ValueError):
    """A metadata file is missing or its header does not match the contract."""


@dataclass(frozen=True)
class FileSpec:
    kind: str
    filename: str
    raw_table: str
    columns: tuple[str, ...]
    required: bool  # must exist in at least one metadata directory


SPECS = (
    FileSpec("access_roles", "access_roles.csv", "raw_access_role",
             ("access_role", "description", "sensitivity_rank"), True),
    FileSpec("user_roles", "user_roles.csv", "raw_user_role",
             ("user_role", "description", "grants"), True),
    FileSpec("business_tables", "business_tables.csv", "raw_business_table",
             ("schema_name", "table_name", "description", "owner", "domain", "tags",
              "access_role", "refresh_frequency", "certified"), True),
    FileSpec("business_columns", "business_columns.csv", "raw_business_column",
             ("schema_name", "table_name", "column_name", "description", "is_pii", "access_role"), False),
    FileSpec("glossary", "glossary.csv", "raw_glossary",
             ("term", "definition", "synonyms", "related_tables"), False),
)


@dataclass
class LoadedFile:
    kind: str
    path: Path
    rows: list[tuple[int, dict[str, str | None]]] = field(default_factory=list)  # (line no, row)


def discover_files(metadata_dirs: list[Path]) -> dict[str, list[Path]]:
    """Files per kind, in layer order (earlier dirs first; later dirs override)."""
    found: dict[str, list[Path]] = {s.kind: [] for s in SPECS}
    for d in metadata_dirs:
        if not d.is_dir():
            raise MetadataSchemaError(f"Metadata directory not found: {d}")
        for spec in SPECS:
            p = d / spec.filename
            if p.is_file():
                found[spec.kind].append(p)
    for spec in SPECS:
        if spec.required and not found[spec.kind]:
            raise MetadataSchemaError(f"Required metadata file '{spec.filename}' not found in {metadata_dirs}")
    return found


def read_file(spec: FileSpec, path: Path) -> LoadedFile:
    with path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        header = [h.strip().lower() for h in (reader.fieldnames or [])]
        missing = [c for c in spec.columns if c not in header]
        if missing:
            raise MetadataSchemaError(f"{path.name}: missing required column(s) {missing}; found {header}")
        extra = [h for h in header if h not in spec.columns]
        if extra:
            log.warning("%s: ignoring unexpected column(s) %s", path.name, extra)
        reader.fieldnames = header
        loaded = LoadedFile(spec.kind, path)
        for row in reader:
            if not any((v or "").strip() for v in row.values() if isinstance(v, str)):
                continue  # fully blank line
            loaded.rows.append((reader.line_num, {c: row.get(c) for c in spec.columns}))
    return loaded


def load_raw(con: sqlite3.Connection, run_id: int, files: dict[str, list[Path]],
             project_root: Path) -> dict[str, int]:
    """Insert every CSV row into its raw_* table. Returns row counts per kind."""
    counts: dict[str, int] = {}
    for spec in SPECS:
        seq = 0
        for path in files[spec.kind]:
            loaded = read_file(spec, path)
            try:
                source = path.resolve().relative_to(project_root).as_posix()
            except ValueError:
                source = path.name
            cols = ("run_id", "source_file", "source_seq", "source_row") + spec.columns
            sql = (f"INSERT INTO {spec.raw_table} ({', '.join(cols)}) "
                   f"VALUES ({', '.join('?' for _ in cols)})")
            batch = []
            for line_no, row in loaded.rows:
                seq += 1
                batch.append((run_id, source, seq, line_no) + tuple(row[c] for c in spec.columns))
            con.executemany(sql, batch)
        counts[spec.kind] = seq
    return counts
