# Design note: Data Catalog Search Assistant (DAI-041)

**Goal.** Help a user find the right dataset in plain language, show table and column evidence for every answer, and never show metadata their access role doesn't permit. Constraints: Python + SQL, synthetic data, runs from a clean setup, AI behind a clear interface, free or local models.

## Key decisions

**1. Split technical from business metadata.** Table and column *existence* is harvested from the warehouse. Hand-written lists drift, and orphaned descriptions are a classic catalog bug. *Meaning and classification* come from steward CSVs. Joining them in SQL exposes both failure modes: steward rows for objects that don't exist go to quarantine as orphans, and warehouse objects without steward rows are flagged as undocumented.

**2. Store raw first, validate in SQL, quarantine instead of dropping.** CSV rows land in `raw_*` unchanged (TEXT). Staging standardises them (trim, lower case, flag parsing), and ordered validation rules set a `reject_reason`. In-file duplicates are resolved with `ROW_NUMBER()` (last valid row wins). Rejected rows are copied to `quarantine` with the reason and the raw record. Reviewers can see exactly what was rejected and why, and fixing the source is easy. A file-level contract break (missing header) **fails the run** instead, because it means the producer changed format and per-row guessing would be wrong.

**3. Idempotent, incremental, atomic runs.** Inputs are fingerprinted (structure plus file bytes, deliberately *not* row counts). Identical inputs give a cheap `skipped` run. Otherwise, the desired state is diffed against the curated tables into `change_log` (added / modified with field list / removed / reactivated), merged with `UPSERT`, and missing objects are soft-deleted. Only changed tables are re-indexed. All of it runs in **one transaction**, so a failure halfway leaves the previous catalog intact (proven by a test that fails the last SQL step).

**4. Fail-closed access, enforced at retrieval.** The `access_role` field on a table (and optionally a column) is the access level required. Roles are granted sets of levels. Unclassified tables default to `restricted` (admin only), and unclassified PII-looking columns default to `pii`. The role filter sits **inside the same SQL query that ranks results**, not in a post-filter or the prompt. So restricted metadata is never retrieved, never sent to a model, and can't be leaked by one. A test inspects the actual prompt to prove this. "No match" responses don't reveal whether restricted data exists.

**5. Lexical search + glossary, not embeddings.** For ~100 metadata documents, SQLite FTS5/BM25 is transparent (you can explain *why* something matched), dependency-free and fast. Its weakness is vocabulary mismatch, so a steward-maintained **business glossary** expands terms ("attrition" → churn, "wages" → compensation). I measured the trade-off on the eval set: hit@1 **0.909 → 0.955**, hit@3 **0.909 → 1.0**, MRR **0.909 → 0.977**. Embeddings would add recall for verbs and paraphrases (the one remaining failure, "bought"), at the cost of a model dependency and opaque matches. That's my first production upgrade (hybrid BM25 + vectors).

**6. AI does the language work, never the access or the facts.** The model (a) rewrites the question into extra keywords and (b) chooses among *retrieved* candidates and explains why. Both outputs are untrusted. Keywords are sanitised and can only add search terms. Answers are parsed defensively and must cite only tables in the evidence. Text naming any other known table rejects the whole answer, unknown columns are dropped with a note, and model confidence can only *lower* retrieval confidence. On rejection, timeout or outage, the system returns the deterministic retrieval answer and records why (`ai_validation`). The whole product therefore works offline, and quality degrades gracefully instead of failing.

**7. Provider-agnostic `LLMClient`.** One abstract method, `generate_json(system, user, schema)`. Implementations: Ollama (local, schema-constrained), OpenAI-compatible (Groq or Gemini free tiers), and offline. Retries cover only transient errors (timeouts, 429, 5xx) with backoff, while 401/400 fail fast. Tests use a scripted fake and `httpx.MockTransport`. No network is needed to run the suite.

## Alternatives considered

| Option | Why not (here) |
|---|---|
| Vector DB + embeddings only | opaque matches, extra service and model download; lexical + glossary already reaches hit@3 = 1.0 on this corpus |
| Let the LLM read the whole catalog | leaks restricted metadata to the model, doesn't scale, ungrounded |
| Post-filter results by role in Python | one forgotten code path = leak; filtering in the retrieval SQL is a single choke point |
| dbt / Airflow / Postgres | correct for production; overkill for a reproducible take-home (SQL is kept portable) |
| Reject the whole steward file on any bad row | one typo would block every good update; quarantine keeps the pipeline flowing and visible |

## Trade-offs and limitations

Keyword recall is limited (eval q24). The access model is coarse (single role, no row-level or attribute policies, role not authenticated). SQLite allows a single writer. The eval set is small and author-labelled. Steward text inside prompts is a residual injection surface: the harmful outcomes are blocked by validation, but explanation text can still be degraded. Production path: warehouse `information_schema` harvest on a scheduler, Postgres + pgvector hybrid search, SSO-group → role mapping shared with warehouse masking policies, and alerting on quarantine and fail-closed counts.
