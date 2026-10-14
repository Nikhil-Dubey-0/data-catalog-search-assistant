# End-to-end walkthrough used in the demo video (Windows PowerShell). Starts from a clean runtime dir.
$ErrorActionPreference = "Continue"
Set-Location (Join-Path $PSScriptRoot "..")
if (-not $env:CATALOG_VAR_DIR) { $env:CATALOG_VAR_DIR = "var_demo" }
function Run { Write-Host "`n> catalog_assistant $args" -ForegroundColor Cyan; python -m catalog_assistant @args }

if (Test-Path $env:CATALOG_VAR_DIR) { Remove-Item -Recurse -Force $env:CATALOG_VAR_DIR }
Write-Host "=== 1. Fresh setup ===" -ForegroundColor Yellow;            Run setup
Write-Host "=== 2. Rerun (idempotency) ===" -ForegroundColor Yellow;      Run build
Write-Host "=== 3. Quarantine / change log ===" -ForegroundColor Yellow; Run runs
Write-Host "=== 4. Same question, different roles ===" -ForegroundColor Yellow
Run ask "employee salaries by department" --role hr_partner
Run ask "employee salaries by department" --role analyst
Write-Host "=== 5. Column-level masking ===" -ForegroundColor Yellow;    Run ask "customer email addresses" --role analyst
Write-Host "=== 6. Glossary + unanswerable ===" -ForegroundColor Yellow
Run ask "client attrition figures" --role analyst
Run ask "quarterly stock price of competitors" --role viewer
Write-Host "=== 7. Evaluation (base catalog) ===" -ForegroundColor Yellow; Run eval --output "$env:CATALOG_VAR_DIR/eval_report.md"
Write-Host "=== 8. Schema change + incremental run ===" -ForegroundColor Yellow
Run simulate-change
Run build --with-changes
Run runs
Run ask "email opens and clicks per campaign" --role analyst
Write-Host "=== 9. Edge case: unknown role ===" -ForegroundColor Yellow; Run ask "revenue" --role ceo
