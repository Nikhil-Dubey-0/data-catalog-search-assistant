# Architecture and data model

![architecture](images/architecture.png)

## Layers

| Layer | Objects | Written by | Behaviour on rerun |
|---|---|---|---|
| Source | `var/warehouse/<schema>.db` (7 files, 16 tables) | `warehouse.py` (seeded) | regenerated only with `--force` |
| Source | `data/metadata/*.csv`, `data/changes/batch_002/*.csv` | data stewards | layered: later directories override earlier ones |
| Control | `etl_run` | `pipeline.py` | one row per run: running → success / skipped / failed |
| Raw | `raw_technical_table/column`, `raw_business_table/column`, `raw_glossary`, `raw_access_role`, `raw_user_role` | Python | append-only, keyed by `run_id`; values kept as received (TEXT) |
| Staging | `stg_business_table/column`, `stg_glossary` | SQL (`10_…`) | rebuilt every run; standardised; `reject_reason` set by validation rules |
| Quarantine | `quarantine` | SQL (`10_…`) | rejected rows + reason + raw record (JSON) per run |
| Curated | `catalog_table`, `catalog_column`, `glossary_term`, `access_role`, `user_role`, `role_grant` | SQL (`20_…`) | UPSERT; soft delete (`is_active = 0`); `first_seen/last_changed/last_seen_run` |
| Change history | `change_log` | SQL (`20_…`) | added / modified (with field list) / removed / reactivated per run |
| Search | `search_doc` (FTS5) | SQL (`30_…`) | only tables in this run's `change_log` re-indexed; full build on cold start |
| Consumption | `v_role_visible_table`, `v_role_visible_column`, `v_metadata_quality`, `v_run_summary`, `search_log` | views / assistant | |

## Pipeline run (python -m catalog_assistant build)

```mermaid
flowchart TD
    A["insert etl_run (running)"] --> B[discover metadata files<br/>check required files + headers]
    B --> C[harvest warehouse read-only<br/>tables, columns, types, null %, distinct]
    C --> D{"fingerprint equals<br/>last successful run?"}
    D -- "yes (and no force flag)" --> S[etl_run = skipped]
    D -- "no" --> T[BEGIN IMMEDIATE]
    T --> R["load raw_* for run_id"]
    R --> ST["10: standardise, validate,<br/>dedupe, quarantine"]
    ST --> M["20: desired state, change_log diff, UPSERT, soft delete"]
    M --> I["30: re-index changed tables"]
    I --> OK["etl_run = success + stats, COMMIT"]
    T -. any exception .-> RB["ROLLBACK, then etl_run = failed + error"]
```

## Ask flow (python -m catalog_assistant ask)

```mermaid
sequenceDiagram
    participant U as User (role)
    participant A as Assistant
    participant L as LLMClient (optional)
    participant S as search.py / SQLite
    U->>A: question, role
    A->>S: check role exists (else error, no model call)
    A->>L: rewrite question -> keywords
    L-->>A: {"keywords": [...]}  (validated, invalid dropped, outage turns AI off for this question)
    A->>S: FTS5 BM25 + glossary groups, filtered by role_grant IN THE SAME QUERY
    S-->>A: candidate tables + matched columns + coverage confidence
    alt no candidates
        A-->>U: "no dataset you have access to" (model not called)
    else candidates
        A->>L: question + role-filtered evidence only
        L-->>A: {"answer", "recommendations", "confidence", "insufficient_evidence"}
        A->>A: validate against evidence (tables, columns, text mentions)
        alt valid
            A-->>U: AI answer + evidence (confidence capped by retrieval)
        else invalid / error / offline
            A-->>U: deterministic answer from retrieval + reason
        end
    end
    A->>S: insert search_log
```

## Entity-relationship (curated + control)

```mermaid
erDiagram
    access_role ||--o{ role_grant : "granted via"
    user_role ||--o{ role_grant : has
    access_role ||--o{ catalog_table : "required by"
    access_role |o--o{ catalog_column : "required by (NULL = inherit)"
    catalog_table ||--o{ catalog_column : contains
    etl_run ||--o{ change_log : records
    etl_run ||--o{ quarantine : rejects
    etl_run ||--o{ raw_technical_table : snapshots
    catalog_table ||--o{ search_doc : "indexed as"

    catalog_table {
        text table_key PK "schema.table"
        text description
        text owner
        text tags
        text access_role FK
        text access_source "steward or default_restricted"
        int certified
        int row_count
        int is_documented
        int is_active
        int first_seen_run
        int last_changed_run
    }
    catalog_column {
        text column_key PK "schema.table.column"
        text table_key FK
        text data_type
        text description
        int is_pii
        text access_role FK "nullable"
        text access_source "steward, pii_flag, auto_pii_name_rule or inherited"
        real null_fraction
        int distinct_count
    }
    role_grant {
        text user_role PK
        text access_role PK
    }
    change_log {
        int run_id FK
        text entity_type
        text entity_key
        text change_type
        text changed_fields
    }
    quarantine {
        int run_id FK
        text entity
        int source_row
        text record_key
        text reason
        text raw_record
    }
```

## Roles and grants (data/metadata)

| user_role | granted access levels |
|---|---|
| viewer | public (the unknown grant `marketing_ro` is quarantined, not applied) |
| analyst | public, internal |
| finance_analyst | public, internal, finance |
| hr_partner | public, internal, hr |
| admin | public, internal, finance, hr, pii, restricted |

## Validation rules (staging, first failing rule wins)

| Entity | Rule | Example in the sample data |
|---|---|---|
| table/column | key present | glossary row with empty term |
| table/column | identifiers match `[a-z0-9_]` after trim/lower | `  SALES ,  Orders ` is *standardised*, not rejected |
| table | `access_role` present and known | `superuser` → rejected → table fails closed to `restricted` |
| column | `access_role` known if given | `secret` |
| table | `certified` ∈ Y/N/yes/no/true/false/1/0/blank | `maybe` |
| column | `is_pii` same | `maybe` |
| table/column | object exists in this run's harvest | `sales.legacy_orders_2019`, `customers.loyalty_points` |
| all | no in-file duplicate key (last valid row wins) | `support.tickets`, `sales.orders.total_amount`, glossary `revenue` |
| grants | granted access level exists | `viewer -> marketing_ro` |
