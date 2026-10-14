"""Command line interface:  python -m catalog_assistant <command> ..."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import textwrap
from pathlib import Path

from . import db
from .assistant import AssistantAnswer, CatalogAssistant
from .config import CHANGE_BATCH_DIR, DEFAULT_METADATA_DIR, EVAL_FILE, Settings
from .evaluate import load_cases, render_markdown, run_eval
from .harvest import WarehouseNotFoundError
from .ingest import MetadataSchemaError
from .llm import create_client
from .pipeline import run_build
from .search import InvalidQueryError, UnknownRoleError, list_roles, visible_tables
from .warehouse import generate_warehouse, simulate_schema_change


def _settings(args) -> Settings:
    return Settings.from_env(Path(args.var_dir).resolve() if args.var_dir else None)


def _open_catalog(settings: Settings):
    if not settings.catalog_db.exists():
        raise SystemExit(f"Catalog not found at {settings.catalog_db}. Run `python -m catalog_assistant setup` first.")
    return db.connect(settings.catalog_db)


def _print_table(rows: list[dict], cols: list[str]) -> None:
    if not rows:
        print("(no rows)")
        return
    widths = {c: min(60, max(len(c), *(len(str(r.get(c, ""))) for r in rows))) for c in cols}
    print("  ".join(c.ljust(widths[c]) for c in cols))
    print("  ".join("-" * widths[c] for c in cols))
    for r in rows:
        print("  ".join(str(r.get(c, ""))[:60].ljust(widths[c]) for c in cols))


def _print_build(result) -> None:
    print(f"run {result.run_id}: {result.status.upper()}")
    if result.status == "success":
        s = result.stats
        print(f"  active tables/columns : {s['active_tables']} / {s['active_columns']}")
        print(f"  changes               : {s['changes'] or 'none'}")
        print(f"  quarantined rows      : {s['quarantined'] or 'none'}")
        print(f"  undocumented tbl/col  : {s['undocumented_tables']} / {s['undocumented_columns']}")
        print(f"  fail-closed tables    : {s['fail_closed_tables']}   auto-PII columns: {s['auto_pii_columns']}")
    else:
        print(f"  {result.stats.get('reason', '')}")


def _print_answer(a: AssistantAnswer) -> None:
    print(f"\nQ [{a.user_role}]: {a.question}")
    print(f"status: {a.status}   confidence: {a.confidence}   provider: {a.provider}   "
          f"AI validation: {a.ai_validation}   ({a.latency_ms} ms)")
    print("search terms: " + ", ".join(a.search_terms))
    print("\n" + textwrap.fill(a.answer, 100) + "\n")
    for i, r in enumerate(a.recommendations, 1):
        print(f"  {i}. {r.table_key}  [access: {r.access_role}{', certified' if r.certified else ''}; "
              f"retrieval confidence: {r.confidence}; source: {r.source}]")
        print(f"     owner: {r.owner or '-'}   matched terms: {', '.join(r.matched_terms)}")
        print("     " + textwrap.fill(f"why: {r.reason}", 95, subsequent_indent="     "))
        for c in r.columns:
            print(f"       - {c['name']} ({c['type']}): {c['description'] or '(undocumented)'}")
        if r.hidden_column_count:
            print(f"       ({r.hidden_column_count} column(s) hidden by your role)")
    for n in a.notes:
        print(f"  note: {n}")


# ------------------------------------------------------------------ commands --
def cmd_init_warehouse(args):
    files = generate_warehouse(_settings(args).warehouse_dir, seed=args.seed, force=args.force)
    print(f"warehouse ready: {len(files)} schema files")


def cmd_build(args):
    dirs = [Path(d) for d in args.metadata] if args.metadata else [DEFAULT_METADATA_DIR]
    if args.with_changes:
        dirs.append(CHANGE_BATCH_DIR)
    _print_build(run_build(_settings(args), dirs, force=args.force))


def cmd_setup(args):
    s = _settings(args)
    generate_warehouse(s.warehouse_dir, force=args.force)
    _print_build(run_build(s, [DEFAULT_METADATA_DIR], force=args.force))


def cmd_simulate_change(args):
    applied = simulate_schema_change(_settings(args).warehouse_dir)
    print("\n".join(applied) if applied else "schema change already applied (nothing to do)")
    print("next: python -m catalog_assistant build --with-changes")


def cmd_ask(args):
    s = _settings(args)
    con = _open_catalog(s)
    assistant = CatalogAssistant(con, create_client(s, args.provider), top_k=args.top_k,
                                 use_query_rewrite=not args.no_rewrite)
    ans = assistant.ask(args.question, args.role)
    if args.json:
        print(json.dumps(ans.to_dict(), indent=2, default=str))
    else:
        _print_answer(ans)


def cmd_browse(args):
    con = _open_catalog(_settings(args))
    _print_table(visible_tables(con, args.role),
                 ["table_key", "access_role", "certified", "row_count", "is_documented", "owner"])


def cmd_roles(args):
    _print_table(list_roles(_open_catalog(_settings(args))), ["user_role", "grants", "description"])


def cmd_runs(args):
    con = _open_catalog(_settings(args))
    _print_table([dict(r) for r in con.execute("SELECT * FROM v_run_summary ORDER BY run_id")],
                 ["run_id", "started_at", "status", "added", "modified", "removed", "quarantined", "error"])
    last = con.execute("SELECT MAX(run_id) FROM etl_run WHERE status = 'success'").fetchone()[0]
    if last:
        print(f"\nchange log (run {last}, tables):")
        _print_table([dict(r) for r in con.execute(
            "SELECT entity_key, change_type, changed_fields FROM change_log "
            "WHERE run_id = ? AND entity_type = 'table'", (last,))],
            ["entity_key", "change_type", "changed_fields"])
        print(f"\nquarantine (run {last}):")
        _print_table([dict(r) for r in con.execute(
            "SELECT entity, source_file, source_row, record_key, reason FROM quarantine WHERE run_id = ?", (last,))],
            ["entity", "source_row", "record_key", "reason"])


def cmd_quality(args):
    con = _open_catalog(_settings(args))
    _print_table([dict(r) for r in con.execute("SELECT * FROM v_metadata_quality ORDER BY documented_pct")],
                 ["table_key", "access_role", "access_source", "active_columns", "undocumented_columns",
                  "pii_columns", "documented_pct"])


def cmd_eval(args):
    s = _settings(args)
    con = _open_catalog(s)
    report = run_eval(con, load_cases(Path(args.file)), create_client(s, args.provider))
    md = render_markdown(report)
    if args.output:
        Path(args.output).write_text(md, encoding="utf-8")
        print(f"report written to {args.output}")
    print(md if not args.output else
          f"pass {report['passed']}/{report['total']}  leaks={report['access_leaks']}  "
          f"glossary hit@1={report['retrieval_fts_glossary']['hit@1']} "
          f"vs fts-only {report['retrieval_fts_only']['hit@1']}")
    if report["access_leaks"]:
        sys.exit(2)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="catalog_assistant", description="Data Catalog Search Assistant")
    p.add_argument("--var-dir", help="runtime directory (default: ./var or $CATALOG_VAR_DIR)")
    p.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("init-warehouse", help="generate the synthetic source warehouse")
    s.add_argument("--seed", type=int, default=42)
    s.add_argument("--force", action="store_true", help="regenerate even if it exists")
    s.set_defaults(func=cmd_init_warehouse)

    s = sub.add_parser("build", help="run the metadata pipeline (incremental, idempotent)")
    s.add_argument("--metadata", action="append", help="metadata dir (repeatable, later overrides earlier)")
    s.add_argument("--with-changes", action="store_true", help="also apply data/changes/batch_002")
    s.add_argument("--force", action="store_true", help="process even if inputs are unchanged")
    s.set_defaults(func=cmd_build)

    s = sub.add_parser("setup", help="init-warehouse + build (fresh start)")
    s.add_argument("--force", action="store_true")
    s.set_defaults(func=cmd_setup)

    s = sub.add_parser("simulate-change", help="apply an upstream schema change to the warehouse")
    s.set_defaults(func=cmd_simulate_change)

    s = sub.add_parser("ask", help="ask the assistant which dataset to use")
    s.add_argument("question")
    s.add_argument("--role", required=True)
    s.add_argument("--provider", choices=["offline", "ollama", "openai_compatible"])
    s.add_argument("--top-k", type=int, default=5)
    s.add_argument("--no-rewrite", action="store_true", help="skip the AI query-rewrite step")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_ask)

    s = sub.add_parser("browse", help="list tables visible to a role")
    s.add_argument("--role", required=True)
    s.set_defaults(func=cmd_browse)

    sub.add_parser("roles", help="list user roles and grants").set_defaults(func=cmd_roles)
    sub.add_parser("runs", help="pipeline run history, latest change log and quarantine").set_defaults(func=cmd_runs)
    sub.add_parser("quality", help="metadata documentation coverage per table").set_defaults(func=cmd_quality)

    s = sub.add_parser("eval", help="run the evaluation set")
    s.add_argument("--file", default=str(EVAL_FILE))
    s.add_argument("--provider", choices=["offline", "ollama", "openai_compatible"])
    s.add_argument("--output", help="write markdown report here")
    s.set_defaults(func=cmd_eval)
    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        args.func(args)
    except (UnknownRoleError, InvalidQueryError, MetadataSchemaError, WarehouseNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
