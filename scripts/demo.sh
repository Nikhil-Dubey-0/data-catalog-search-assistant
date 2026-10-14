#!/usr/bin/env bash
# End-to-end walkthrough used in the demo video. Starts from a clean runtime dir.
set -euo pipefail
cd "$(dirname "$0")/.."
export CATALOG_VAR_DIR="${CATALOG_VAR_DIR:-var_demo}"
PY="${PYTHON:-python}"
run() { echo; echo "\$ catalog_assistant $*"; $PY -m catalog_assistant "$@"; }

rm -rf "$CATALOG_VAR_DIR"
echo "=== 1. Fresh setup: generate synthetic warehouse + first pipeline run ==="
run setup
echo; echo "=== 2. Rerun with identical inputs (idempotency) ==="
run build
echo; echo "=== 3. What did the pipeline reject? ==="
run runs
echo; echo "=== 4. Same question, different roles ==="
run ask "employee salaries by department" --role hr_partner
run ask "employee salaries by department" --role analyst
echo; echo "=== 5. Column-level masking ==="
run ask "customer email addresses" --role analyst
echo; echo "=== 6. Synonyms via glossary, and an unanswerable question ==="
run ask "client attrition figures" --role analyst
run ask "quarterly stock price of competitors" --role viewer
echo; echo "=== 7. Evaluation set (base catalog) ==="
run eval --output "$CATALOG_VAR_DIR/eval_report.md"
echo; echo "=== 8. Upstream schema change + new steward metadata (incremental run) ==="
run simulate-change
run build --with-changes
run runs
run ask "email opens and clicks per campaign" --role analyst
echo; echo "=== 9. Edge case: unknown role is rejected ==="
run ask "revenue" --role ceo || true
