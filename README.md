# Data Catalog Search Assistant (DAI-041)

> **Assignment DAI-041 · AI + Data · Nikhil Dubey**
> *"Create metadata for a small set of tables/columns and build a search assistant that helps users find the
> right dataset. Results must return table/column evidence and respect a simple access-role field."*

A small, reproducible data product. A metadata pipeline harvests technical metadata from a (synthetic) warehouse and merges it with data-steward CSVs into a curated catalog. A search assistant then answers *"which dataset should I use for X?"*. Every answer comes with **table and column evidence** and is **filtered by the user's access role** before any AI sees it.

| | |
|---|---|
| Design note (1 page) | [docs/design_note.md](docs/design_note.md) |
| Architecture & data model | [docs/architecture.md](docs/architecture.md) |
| Testing evidence | [docs/testing_evidence.md](docs/testing_evidence.md) · [docs/eval_report.md](docs/eval_report.md) |
| Demo video | *link to be added* · script: [docs/demo_video_script.md](docs/demo_video_script.md) |
| Submission summary (PDF) | [docs/SUBMISSION_SUMMARY.pdf](docs/SUBMISSION_SUMMARY.pdf) |

---

## 1. Problem statement

Analysts waste time finding the right table, and keyword search over a wiki doesn't know who is allowed to see what. The assistant must:

1. hold **metadata** for tables and columns (descriptions, owners, types, tags, PII flags, an **access role**);
2. let a user describe the data they need in plain language and get the **right dataset(s)** back;
3. show **evidence** for every recommendation: the table, its description and owner, and the specific matching columns;
4. **respect access roles**: a user must never see, or be told about, tables or columns their role isn't granted.

## 2. Approach (summary)

| Concern | Decision |
|---|---|
| Where metadata comes from | **Technical** metadata is *harvested* from the warehouse (truth about what exists). **Business** metadata comes from steward CSVs (descriptions, owner, access role, PII). Both are merged in SQL. |
| Data quality | Raw rows are stored as received. SQL staging standardises and validates them. Bad rows go to a **quarantine** table with a reason; they are never silently dropped. |
| Re-runs / incremental | Input fingerprint → unchanged inputs give a **skipped** run. Changes are diffed into `change_log`, merged with **UPSERT**, and dropped objects are **soft-deleted**. Only changed tables are re-indexed. The whole run is **one transaction**. |
| Access control | **Fail closed.** A table without a valid steward classification becomes `restricted` (admin only). A PII-named column without classification becomes `pii`. The role filter is part of the **retrieval SQL**, so unauthorised metadata is never retrieved and so can never reach the model. |
| Search | SQLite **FTS5 / BM25** over table and column documents, plus **glossary synonym expansion** (measured: hit@1 0.909 → 0.955). |
| AI | Behind one `LLMClient` interface (Ollama local, or any OpenAI-compatible free tier, or offline). AI does two things: (1) rewrites the question into extra search keywords, (2) picks datasets from the retrieved evidence and explains why. **Every AI output is validated** against the evidence. Anything citing a table outside it is rejected, and the answer falls back to deterministic retrieval. |

## 3. Architecture / data flow

![architecture](docs/images/architecture.png)

```
warehouse (7 SQLite schemas) ──harvest──┐
steward CSVs (layered dirs) ──load──────┤
                                        ▼
                raw_* (append-only per run_id)  ── fingerprint unchanged? → run "skipped"
                                        ▼
         stg_* (SQL: standardise, validate, dedupe) ── rejects → quarantine (+reason, raw JSON)
                                        ▼
     catalog_table / catalog_column / glossary_term / role_grant   ── diff → change_log
                                        ▼
                   search_doc (FTS5; only changed tables re-indexed)
                                        ▼
question + role → [AI rewrite] → role-filtered BM25 retrieval → evidence → [AI answer] → validator
                                        ▼                                         │ rejected / error / offline
                         CLI · Streamlit · search_log · eval report  ◄── deterministic fallback
```

More detail, including the ER diagram and the ask sequence: [docs/architecture.md](docs/architecture.md).

## 4. Technologies

| Purpose | Choice | Why |
|---|---|---|
| Language | Python 3.10+ (tested on 3.13) | Required: Python + SQL |
| Storage + transforms | **SQLite** (stdlib) with window functions, recursive CTEs, UPSERT, JSON1, **FTS5** | Zero setup, runs from a clean clone, real SQL |
| HTTP to models | `httpx` | Timeouts, mock transport for tests |
| Local LLM (optional) | **Ollama** (e.g. `qwen2.5:3b`) | Free, local, JSON-schema-constrained output |
| Hosted LLM (optional) | Any **OpenAI-compatible** endpoint, e.g. Groq free tier `openai/gpt-oss-20b` | Free tier, one config switch |
| UI | Streamlit | Quick demo UI over the same Python API |
| Tests | pytest | 49 tests |

No ORM, no vector database, no orchestration framework. The data volume doesn't justify them (see the design note).

## 5. Project structure

```
├── README.md
├── requirements.txt / pyproject.toml   dependencies; package installed with `-e .`
├── .env.example                        configuration template (no secrets committed)
├── sql/
│   ├── 01_catalog_schema.sql           raw / staging / curated / control / FTS5 tables
│   ├── 02_views.sql                    role-visibility views, metadata quality, run summary
│   ├── 10_stage_business_metadata.sql  standardise + validate + dedupe + quarantine
│   ├── 20_merge_catalog.sql            desired state, change_log diff, UPSERT, soft delete
│   └── 30_refresh_search_index.sql     incremental FTS5 refresh
├── src/catalog_assistant/
│   ├── warehouse.py    synthetic source warehouse + simulated schema change
│   ├── harvest.py      technical metadata harvesting + profiling
│   ├── ingest.py       CSV contract checks, raw load
│   ├── pipeline.py     run orchestration: fingerprint, transaction, run log
│   ├── search.py       role-aware retrieval, glossary expansion, evidence, confidence
│   ├── assistant.py    rewrite → retrieve → answer → validate → fallback → log
│   ├── validation.py   JSON parsing + evidence checks for model output
│   ├── prompts.py      prompts and JSON schemas
│   ├── llm/            LLMClient interface, Ollama + OpenAI-compatible clients, factory
│   ├── evaluate.py     evaluation harness
│   └── cli.py          command line interface
├── app/streamlit_app.py
├── data/
│   ├── metadata/       steward CSVs (with deliberate quality problems)
│   ├── changes/batch_002/  second metadata delivery for the incremental demo
│   └── eval/eval_questions.csv  29 labelled questions
├── tests/              49 tests
├── scripts/            demo.sh / demo.ps1, draw_architecture.py, build_summary_pdf.py
└── docs/               design note, architecture, testing evidence, eval report, demo script
```

## 6. Setup (clean machine)

Requires Python ≥ 3.10 and git.

```bash
git clone <repo-url> data-catalog-search-assistant
cd data-catalog-search-assistant
python -m venv .venv
# Windows: .venv\Scripts\activate     macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # optional; defaults work offline (Windows: copy .env.example .env)
```

**Optional AI provider** (the project runs fully without one):

```bash
# Local and free: install Ollama from https://ollama.com, then
ollama pull qwen2.5:3b
# and in .env:  LLM_PROVIDER=ollama

# or a free hosted tier, e.g. Groq, in .env:
# LLM_PROVIDER=openai_compatible
# LLM_BASE_URL=https://api.groq.com/openai/v1
# LLM_MODEL=openai/gpt-oss-20b
# LLM_API_KEY=<your key>          # .env is git-ignored
```

## 7. How to run

```bash
python -m catalog_assistant setup                     # generate warehouse + first pipeline run
python -m catalog_assistant build                     # rerun -> SKIPPED (inputs unchanged)
python -m catalog_assistant runs                      # run history, change log, quarantine
python -m catalog_assistant quality                   # documentation coverage per table

python -m catalog_assistant ask "employee salaries by department" --role hr_partner
python -m catalog_assistant ask "employee salaries by department" --role analyst      # -> no match
python -m catalog_assistant ask "customer email addresses" --role analyst             # PII columns hidden
python -m catalog_assistant ask "client attrition figures" --role analyst --provider ollama
python -m catalog_assistant browse --role viewer
python -m catalog_assistant roles

python -m catalog_assistant simulate-change           # upstream schema change
python -m catalog_assistant build --with-changes      # incremental run with metadata batch 002

python -m catalog_assistant eval --output docs/eval_report.md
pytest                                                # 49 tests

streamlit run app/streamlit_app.py                    # web UI
bash scripts/demo.sh    # or: powershell -File scripts/demo.ps1   (full walkthrough from scratch)
```

`--json` on `ask` prints the full structured answer (evidence, notes, validation status).

## 8. Data model

Full ER diagram in [docs/architecture.md](docs/architecture.md). Key curated tables:

| Table | Grain | Notable columns |
|---|---|---|
| `catalog_table` | one row per warehouse table | `table_key` (schema.table), description, owner, tags, **`access_role`**, `access_source` (steward / default_restricted), certified, row_count, is_documented, is_active, first_seen / last_changed / last_seen run |
| `catalog_column` | one row per column | data_type, nullable, pk, description, `is_pii`, **`access_role`** (NULL = inherit table), `access_source` (steward / pii_flag / auto_pii_name_rule / inherited), null_fraction, distinct_count |
| `access_role` | access level | public, internal, finance, hr, pii, restricted (+ sensitivity rank) |
| `user_role`, `role_grant` | role → granted access levels | viewer, analyst, finance_analyst, hr_partner, admin |
| `glossary_term` | business term | synonyms, related tables |
| `etl_run`, `change_log`, `quarantine`, `search_log` | control / audit | |
| `search_doc` | FTS5 document per table and column | role columns stored UNINDEXED for filtering |

**Access rule:** a user sees a table if `role_grant(user_role, table.access_role)` exists. They see a column if the table is visible **and** `role_grant(user_role, COALESCE(column.access_role, table.access_role))` exists. The same rule is used by the views (`v_role_visible_table`, `v_role_visible_column`) and by the retrieval query.

**Synthetic data:** 7 schemas / 16 tables / 99 columns, generated from a fixed seed (`warehouse.py`). The steward CSVs contain **deliberate defects**: in-file duplicates (conflicting and exact), a whitespace/case variant key, an unknown access role (`superuser`, `secret`), an invalid flag (`maybe`), orphan rows for a non-existent table/column, blank descriptions, a missing glossary term, and an unknown grant.

## 9. AI approach

| | Query rewrite | Answer synthesis |
|---|---|---|
| Input to model | the question only | question, role, and **only role-filtered evidence**: up to 5 candidate tables with description, owner, certification, matched terms, retrieval confidence and visible columns |
| Expected output | `{"keywords": [...]}` | `{"answer", "recommendations": [{"table", "columns", "reason"}], "confidence", "insufficient_evidence"}` |
| Validation | each keyword must match `[a-z0-9 _-]{1,40}`; invalid ones dropped; keywords can only *add* search terms, never widen access | JSON parsed defensively; every `table` must be in the evidence (else **whole answer rejected**); text mentioning any known table outside the evidence → rejected (hallucination/leak); unknown columns dropped with a note; model confidence can only *lower* retrieval confidence |
| On failure | ignored, deterministic search | **deterministic answer** from retrieval, with the reason recorded in `ai_validation` and `notes` |
| Stored | `search_log` (role, question, status, provider, validation status, top tables, latency) | same |

If retrieval finds nothing the role may see, the model is **not called** and the user gets a neutral "no dataset you have access to" message. It doesn't confirm whether restricted data exists.

## 10. Assumptions

- Warehouse schema = one SQLite file; table identity = `schema.table`. The warehouse is the source of truth for *existence*; stewards own *meaning and classification*.
- `access_role` on a table/column is the **access level required**; user roles are granted sets of levels (`user_roles.csv`).
- Unclassified data is restricted (**fail closed**) until a steward classifies it. Columns named like e-mail/phone/bank/ssn/passport/birth/address default to `pii` when unclassified.
- In one steward file, the **last valid row** for a key wins. Across layered directories, later directories override earlier ones (a change batch is an intentional override).
- A missing column/table in the warehouse is a **soft delete** (kept for history, removed from search).
- Row-count changes alone are not catalog changes (no re-index churn).
- Revealing *how many* columns are hidden by your role is acceptable; revealing *names* is not.
- Eval labels describe the **base** catalog (before change batch 002).

## 11. Tests performed

49 pytest tests plus an evaluation set. Details and full output: [docs/testing_evidence.md](docs/testing_evidence.md).

| Area | What is proven (incl. failure / edge cases) |
|---|---|
| Pipeline | duplicates resolved to the last valid row; every defect quarantined with the right reason; unclassified table fails closed; idempotent rerun is skipped and a forced rerun changes nothing; incremental schema change produces correct added/modified/removed and auto-PII; **header drift fails the run and leaves the catalog untouched**; **failure mid-transform rolls everything back**; missing warehouse gives a clear error |
| Access | roles see only granted tables; PII columns hidden; an unknown grant isn't widened; unknown role rejected; **the prompt sent to the model contains no restricted tables/columns** |
| Search | glossary bridges vocabulary gaps; evidence present; **FTS syntax/SQL-injection strings are harmless**; empty / oversized / stopword-only / unrelated questions |
| AI | valid answer used and bad columns dropped; **hallucinated table rejected**; **answer naming a restricted table rejected**; malformed JSON; **model outage** (no second call); no evidence → no model call; confidence capped; insufficient-evidence path; keyword sanitising |
| LLM clients | request format (Ollama schema / OpenAI JSON mode / bearer token), **retry on 503 + timeout**, no retry on 401, give-up after N, bad response shape |
| Eval gate | 0 policy leaks, 0 label violations, pass rate ≥ 90 %, glossary ≥ FTS-only |

Mutation check: deliberately breaking the column-access filter or the leak validator makes 4 tests fail (see testing evidence).

## 12. Sample output

```
$ python -m catalog_assistant ask "employee salaries by department" --role hr_partner
status: answered   confidence: high   provider: offline   AI validation: not_called: offline mode
search terms: compensation (glossary), employee (glossary), department (query)

Best match: hr.employees - Employee directory with department, job title, manager, hire date and
salary. Relevant columns: salary, department, employee_id, full_name, hire_date. ...
  1. hr.employees  [access: hr, certified; retrieval confidence: high; source: retrieval]
       - salary (REAL): Annual base salary.
       - department (TEXT): Department the employee belongs to.
  2. hr.payroll  [access: hr; retrieval confidence: medium; source: retrieval]
       ...
       (1 column(s) hidden by your role)

$ python -m catalog_assistant ask "employee salaries by department" --role analyst
status: no_match ... I could not find a dataset you have access to that matches this question.
```

Evaluation (offline, base catalog): **28/29 pass, 0 policy leaks, 0 label violations**. Retrieval: FTS only hit@1 0.909 / MRR 0.909; **with glossary hit@1 0.955 / hit@3 1.0 / MRR 0.977**. Full transcript: [docs/demo_transcript.txt](docs/demo_transcript.txt).

## 13. Known limitations

- **Keyword retrieval:** "which customers *bought* which products" fails offline (eval q24) because "bought" isn't in any metadata or glossary. The AI query rewrite or embeddings would fix this; I left the failure in rather than tune the glossary to the test.
- The real-model path is covered by contract tests (mock HTTP) and the scripted-model tests. Answer quality depends on the model chosen. A small local model may be rejected by the validator more often (the system stays correct, just less helpful).
- Metadata descriptions are steward-written text placed in the prompt. The prompt marks them as data, and the validator blocks the harmful *outcomes* (unknown tables, leaks), but a determined injection could still degrade the explanation text.
- Access model is single-role and coarse (no row-level security, no attribute-based policies, no per-user identity). The role is passed in, not authenticated.
- SQLite = single writer; fine for a catalog of this size, not for concurrent multi-team writes.
- Eval set is small (29 questions) and written by the author, so it can't rule out overfitting.

## 14. What I would change for production

- Harvest from the real warehouse's `information_schema` (Snowflake/BigQuery/Postgres) on a schedule (Airflow/Dagster), and take steward input via a PR-reviewed YAML repo or a catalog UI instead of CSV.
- Postgres for the catalog; keep the SQL transforms (or move them to dbt with tests); `tsvector` or OpenSearch for search, plus **hybrid** retrieval with embeddings (pgvector) for verb/paraphrase recall.
- Real identity: map SSO groups → roles; enforce the same policy as the warehouse (one source of truth, e.g. tag-based masking policies); audit log retention.
- Alerting on quarantine counts and fail-closed tables; data-contract checks in CI for steward files.
- Larger, reviewer-labelled eval set run in CI against the chosen model; track validator rejection rate per model version.
- Response caching and a cheaper single-call mode (rewrite + answer) to control latency and cost.

## 15. Security

No secrets in the repository: configuration via environment variables / `.env` (git-ignored); `.env.example` documents keys without values. All data is synthetic (e-mails use `example.com`). The warehouse is opened read-only during harvesting.

## 16. External resources

Python stdlib `sqlite3` (FTS5, JSON1), [httpx](https://www.python-httpx.org/), [Streamlit](https://streamlit.io/), [pytest](https://pytest.org/), [Ollama API: structured outputs](https://ollama.com/blog/structured-outputs), [Groq models](https://console.groq.com/docs/models). AI coding assistants were used as a development aid (see the commit trailers).
