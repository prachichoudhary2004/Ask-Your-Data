# Architecture Decisions

This file was written **after** the project was built. It reconstructs each decision from the git history (`git log -p`), commit messages, and code comments. Where a reason is stated in a commit message or code comment, the source is cited. Where it was not recorded at the time, the entry says so.

Each entry follows this layout:
- **ID / Title** — short label
- **What was decided** — the choice made
- **Options considered** — alternatives that were visible
- **Reason** — why, plus source (commit hash, code comment, or *inferred*)
- **Trade-off** — what was given up
- **Status** — Accepted / Rejected / Temporary / Superseded
- **Files** — which files are affected
- **Commit** — the commit where this landed

---

## D-01 · Fictional company database (XPTO Empresa)

**What was decided:** Use a made-up Brazilian company ("XPTO Empresa") instead of a real or public dataset.

**Options considered:** Public Northwind, Chinook, or a real company dump.

**Reason:** A fictional company avoids data-privacy issues and lets the schema be designed to cover a wide variety of SQL patterns (joins, aggregates, date functions). Reason inferred from code, not recorded at the time.

**Trade-off:** Results cannot be compared against an external benchmark leaderboard. The domain is narrow (one company), so generalisation is unknown.

**Status:** Accepted

**Files:** `scripts/create_xpto_db.py`, `data/xpto_empresa.db`

**Commit:** `fdfb641` (initial commit)

---

## D-02 · Portuguese schema names, English and Portuguese questions

**What was decided:** Column and table names are in Portuguese (`vendas`, `clientes`, `funcionarios`, `data_venda`, etc.). The evaluation set contains both English and Portuguese questions (10 Portuguese in `eval/questions.json`).

**Options considered:** English-only schema; separate English/Portuguese collections.

**Reason:** The fictional company is Brazilian, so Portuguese column names reflect a realistic scenario. Reason inferred from code, not recorded at the time.

**Trade-off:** The LLM must map English words like "sales" to Portuguese column names like `data_venda`. This is a hard part of the task. The multilingual embedding model (D-05) was chosen partly for this reason.

**Status:** Accepted

**Files:** `scripts/create_xpto_db.py`, `eval/questions.json`

**Commit:** `fdfb641`

---

## D-03 · SQLite as the database engine

**What was decided:** Use SQLite for the company database.

**Options considered:** PostgreSQL, DuckDB.

**Reason:** Zero-infrastructure setup; no server process needed; easy to distribute as a single file. Reason inferred from code, not recorded at the time.

**Trade-off:** SQLite has quirks (`strftime` for date functions, no `FULL OUTER JOIN`) that differ from production databases. Generated SQL may not transfer.

**Status:** Accepted

**Files:** `sql_executor.py`, `sql_validator.py`, `scripts/create_xpto_db.py`

**Commit:** `fdfb641`

---

## D-04 · ChromaDB as the vector store

**What was decided:** Use ChromaDB (persisted to disk) for the RAG retrieval index.

**Options considered:** FAISS, Pinecone, Weaviate.

**Reason:** Inferred from code, not recorded at the time. ChromaDB integrates directly with LangChain, supports local persistence without a server, and is free.

**Trade-off:** ChromaDB is not production-grade for large corpora. Scaling beyond the XPTO dataset would require migration.

**Status:** Accepted

**Files:** `retriever_node.py`, `ingest_pipeline.py`

**Commit:** `fdfb641`

---

## D-05 · Embedding model: `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`

**What was decided:** Use the multilingual paraphrase MiniLM model for embeddings.

**Options considered:** English-only models (e.g., `all-MiniLM-L6-v2`).

**Reason:** The model name is set in `.env-example`. The model is multilingual, which is needed because questions can be in English or Portuguese. Reason inferred from code; not explicitly stated in a commit message.

**Trade-off:** The multilingual model is slightly larger and slower than English-only alternatives.

**Status:** Accepted

**Files:** `retriever_node.py`, `ingest_pipeline.py`, `.env-example`

**Commit:** `fdfb641`

---

## D-06 · Cross-encoder reranking; retrieve TOP_K × 3 first

**What was decided:** Retrieve `TOP_K * 3` documents from ChromaDB, then rerank with a `CrossEncoder` and keep the top `TOP_K`.

**Options considered:** No reranking; BM25 hybrid search.

**Reason:** Code comment in `retriever_node.py`: *"Increments the number of documents retrieved for reranking, to allow the rerank model to choose the best ones."*

**Trade-off:** Doubles the retrieval time. The rerank model (`cross-encoder/ms-marco-MiniLM-L-6-v2`) loads at startup and adds latency on the first request.

**Status:** Accepted

**Files:** `retriever_node.py`

**Commit:** `fdfb641`

---

## D-07 · Four document types in the ChromaDB index

**What was decided:** Index four kinds of documents per table: row-level text, table-level schema, column-level schema, and few-shot Q/SQL examples.

**Options considered:** Row-level text only (the original `fdfb641` behaviour).

**Reason:** Commit message `d2f31d2`: *"Step 2: schema-aware indexing."* The module docstring says the enriched index *"reduces hallucinations such as referencing tables that do not exist."*

**Trade-off:** Larger ChromaDB collection; longer ingest time. The few-shot examples in `ingest_pipeline.py` use hardcoded generic English examples that reference tables that do not exist in the XPTO schema (e.g., `orders`, `products`). This is a known gap.

**Status:** Accepted

**Files:** `ingest_pipeline.py`

**Commit:** `d2f31d2`

---

## D-08 · Full allowed-schema injected into every LLM prompt

**What was decided:** Every call to the SQL-generation LLM includes the complete list of allowed tables and their columns (produced by `_allowed_tables_schema()` in `sql_node.py`).

**Options considered:** Relying on RAG context alone.

**Reason:** Module docstring of `sql_node.py`: *"This reduces hallucinations such as referencing tables that do not exist."* The same reason is implied by commit `d2f31d2`. The LLM was previously inventing non-existent table names (e.g., "orders").

**Trade-off:** Every prompt is longer, which increases token cost and latency slightly.

**Status:** Accepted

**Files:** `sql_node.py`

**Commit:** `d2f31d2`

---

## D-09 · Full schema also injected into the retry prompt

**What was decided:** The `_llm_retry()` function in `query_service.py` calls `_allowed_tables_schema()` and includes the result in the retry prompt.

**Options considered:** Retry prompt with only the question, failed SQL, and error message (the original implementation in `8ca81f7`).

**Reason:** Commit message `e01cbc6`: *"Include schema in retry prompt."* The prior prompt produced hallucinated table names such as "sales" because it had no schema reference.

**Trade-off:** Slightly larger retry prompt.

**Status:** Accepted

**Files:** `query_service.py`

**Commit:** `e01cbc6`

---

## D-10 · Groq as the LLM provider; model switch from Gemini to Llama to openai/gpt-oss-120b

**What was decided:** The project started with `langchain_google_genai` (Gemini 2.0 Flash), switched to Groq with `llama-3.3-70b-versatile`, and the final evaluation was run with `openai/gpt-oss-120b` on Groq.

**Options considered:** Staying on Gemini; OpenAI directly.

**Reason:** The switch from Gemini to Groq is recorded in commit `e04ce97`: *"Switch LLM to Groq (llama-3.3-70b-versatile), update deps and README."* No reason is given in the commit message. The final switch to `openai/gpt-oss-120b` was most likely made during an AI coding-agent session, not as a deliberate human decision. No commit records this model change; it appears to have been applied to `.env` directly.

**Trade-off:** Groq is fast but adds an external API dependency. The model name in `.env` can be changed freely; the code reads it from the environment.

**Status:** Accepted (the Gemini path is Superseded)

**Files:** `sql_node.py`, `query_service.py`, `.env-example`

**Commit:** `e04ce97` (Gemini → Groq); final model change not in git history

---

## D-11 · SQL validation: exact checks performed

**What was decided:** `validate_sql()` in `sql_validator.py` checks:
1. No semicolon in the string (blocks multiple statements).
2. No disallowed keyword: `insert`, `update`, `delete`, `drop`, `alter`, `create`, `replace`, `attach`, `detach`, `truncate`, `pragma`, `vacuum`, `merge`.
3. Parse succeeds with `sqlglot` in SQLite dialect.
4. Parsed statement type is `SELECT`.
5. All tables used are in the allowed-tables set.

**What it does NOT check:** column names, SQL injection patterns beyond the keyword list, subquery depth, or UNION abuse.

**Reason:** Inferred from code. The goal is to block write operations and hallucinated table names. Keyword matching is case-insensitive (padded with spaces to avoid false positives inside identifiers).

**Trade-off:** A determined adversary could still inject through allowed constructs. This is a research/demo project, not a production security layer.

**Status:** Accepted

**Files:** `sql_validator.py`

**Commit:** `fdfb641`, refined in `15cd07d`

---

## D-12 · Metadata tables excluded from allowed tables

**What was decided:** `table_comments` and `column_comments` are excluded from the allowed-tables set returned by `allowed_tables_from_db()`.

**Options considered:** Leaving them in (they are valid SQLite tables).

**Reason:** Commit `15cd07d`: *"Default TOP_K and exclude metadata tables from allowed tables."* No further explanation, but the intent is clear: users should not be able to query internal metadata tables.

**Trade-off:** If the LLM tries to use metadata tables it gets a validation error. This is the desired behaviour.

**Status:** Accepted

**Files:** `sql_validator.py`

**Commit:** `15cd07d`

---

## D-13 · Read-only SQLite connection

**What was decided:** `sql_executor.py` opens SQLite with `?mode=ro` (read-only URI). `sql_node.py` and `eval/regrade.py` do the same.

**Options considered:** Normal read-write connection with write operations blocked only by the keyword validator.

**Reason:** Inferred from code. Defence in depth: even if the keyword validator misses something, the OS-level read-only flag prevents writes.

**Trade-off:** None meaningful for a read-only workload.

**Status:** Accepted

**Files:** `sql_executor.py`, `sql_node.py`, `eval/regrade.py`

**Commit:** `fdfb641`

---

## D-14 · Automatic LIMIT clause added by executor

**What was decided:** `sql_executor.py::enforce_limit()` appends `LIMIT 1000` to any query that does not already have a LIMIT clause. `fetchmany(row_limit)` is also applied.

**Options considered:** No limit; let the user add it.

**Reason:** Inferred from code. Prevents the UI from being flooded with thousands of rows.

**Trade-off:** Queries that legitimately need all rows are silently capped.

**Status:** Accepted

**Files:** `sql_executor.py`

**Commit:** `fdfb641`

---

## D-15 · Retry loop: up to 2 retries; which errors trigger it

**What was decided:** `query_service.py::query()` tries up to 3 times (1 initial + 2 retries). A retry is triggered when:
- `validate_sql()` returns `False` (validation error stored in `error_msg`).
- `execute_sql()` raises any exception (stored in `error_msg`).

A retry is **not** triggered when:
- The LLM API itself fails (this raises immediately and is caught by FastAPI as a 500).
- The query returns wrong rows (there is no correctness check).

**Reason:** Commit `8ca81f7`: *"Step 3: retry loop."*

**Trade-off:** Two retries add up to 3× the latency if all fail. The retry loop never ran in the benchmark: every question passed on the first attempt.

**Status:** Accepted

**Files:** `query_service.py`

**Commit:** `8ca81f7`

---

## D-16 · FastAPI + Streamlit as two separate processes

**What was decided:** `api.py` runs as a FastAPI/Uvicorn server. `ui.py` is a Streamlit app that calls it over HTTP on `localhost:8000`.

**Options considered:** Putting everything in one Streamlit app; using a single Flask server.

**Reason:** Inferred from code. Decoupling the API lets the evaluation harness (`eval/run_eval.py`) call the API directly via `query_service.py` without going through HTTP, while the UI uses HTTP. It also lets the API be replaced or extended independently.

**Trade-off:** Users must start two processes. If the API is down, the UI shows an error banner.

**Status:** Accepted

**Files:** `api.py`, `ui.py`

**Commit:** `fdfb641`

---

## D-17 · Evaluation design: gold SQL, row comparison, caching, backoff

**What was decided:**
- Each question in `eval/questions.json` has a `gold_sql` field.
- The harness runs both the pipeline and the gold SQL, then compares result sets row by row (normalised to lowercase strings, float values rounded to 4 d.p.).
- LLM responses are cached in `eval/cache/` keyed by `(question_text, prompt_hash)` to avoid re-billing on reruns.
- Rate-limit (429) errors trigger exponential backoff (base 4 s, max 300 s, max 5 retries).

**Reason:** Docstring of `eval/run_eval.py` describes all four outcomes (`correct`, `wrong`, `validator_rejected`, `infra_error`) and the caching strategy.

**Trade-off:** Row comparison ignores column names, so a query that returns the right values with wrong aliases counts as "correct" in the original harness. This led to the regrade script (D-18).

**Status:** Accepted

**Files:** `eval/run_eval.py`, `eval/questions.json`

**Commit:** `55ff405`

---

## D-18 · Regrade script: strict vs relaxed; relaxed chosen after seeing results

**What was decided:** `eval/regrade.py` applies two scoring rules to the already-generated SQL (no LLM calls):
- **STRICT:** column names and row values must both match.
- **RELAXED:** row count must match; every column's sorted value-list must match some column in the gold result (column names ignored).

**Options considered:** Fixing the original harness.

**Reason:** Commit `39116ff`: *"Add regrade script with relaxed row-level scoring."* The relaxed rule was designed after seeing the original 52.5% result. It was chosen to give credit for queries that return the right data but with different column aliases. **This is a methodological limit: the relaxed rule was not pre-specified before running the evaluation.**

**Trade-off:** Relaxed scoring inflates the reported accuracy. Strict scoring (22.5%) is more conservative but penalises reasonable alias choices.

**Status:** Accepted (with the caveat above)

**Files:** `eval/regrade.py`

**Commit:** `39116ff`

---

## D-19 · Measure forward; no pre-Step-2 baseline rebuilt

**What was decided:** The only benchmark run kept in `eval/results/` is the final one (`results-20261029T192658Z.json`). Old results were deleted in commit `ed218cc`. No pre-schema-aware baseline was re-run for comparison.

**Options considered:** Rebuilding a baseline from the original pipeline.

**Reason:** Commit `ed218cc`: *"Remove old eval results, cache and debug script."* No comparison baseline was recorded.

**Trade-off:** There is no measured improvement delta to report. We cannot say how much the schema injection helped.

**Status:** Accepted

**Files:** `eval/results/`

**Commit:** `ed218cc`

---

## D-20 · Remove duplicate code from sql_node.py and ingest_pipeline.py

**What was decided:** Two commits (`86038a8`, `4d54c07`) removed large blocks of duplicated code from `sql_node.py` and `ingest_pipeline.py`. The kept half is the schema-aware version.

**Reason:** Commit `86038a8`: *"Remove duplicate code in sql_node."* Commit `4d54c07`: *"Remove duplicate code in ingest_pipeline."*

**Trade-off:** None. The duplicate was dead code left from an AI coding-agent session.

**Status:** Accepted

**Files:** `sql_node.py`, `ingest_pipeline.py`

**Commits:** `86038a8`, `4d54c07`

---

## D-21 · Chat-style UI with history in `session_state`

**What was decided:** `ui.py` stores all past messages in `st.session_state.messages`. Each turn is replayed on re-render. A single `call_api()` function handles all HTTP calls to the FastAPI backend.

**Options considered:** Stateless page (one question at a time).

**Reason:** Commit `5526cdc`: *"Chat UI with automatic charts."*

**Trade-off:** History is lost on page refresh. There is no multi-turn SQL refinement; each question is independent.

**Status:** Accepted

**Files:** `ui.py`

**Commit:** `5526cdc`

---

## D-22 · Automatic chart rules in `chart_utils.py`

**What was decided:** After every query, `choose_chart()` inspects columns and values and returns a chart spec (`bar`, `line`, or `None`). Rules (in priority order): (1) first column is date-like → line chart; (2) exactly one text column + one numeric column + ≤30 rows → bar chart; (3) otherwise no chart.

**Options considered:** Always show a table; let the user choose a chart type.

**Reason:** Commit `3982763`: *"add API latency/attempts fields and chart helper."* No further reason is given.

**Trade-off:** Rules are heuristic. They can produce misleading charts (e.g., a bar chart for unrelated string/number pairs).

**Status:** Accepted

**Files:** `chart_utils.py`, `ui.py`

**Commit:** `3982763`

---

## D-23 · Tests that avoid importing the AI pipeline

**What was decided:** `tests/test_validator.py`, `tests/test_executor.py`, and `tests/test_chart_utils.py` only import `sql_validator`, `sql_executor`, and `chart_utils`. They do not import `query_service`, `retriever_node`, `sql_node`, or `api`. Temporary SQLite files are created with `tempfile`.

**Reason:** Test docstrings state: *"Does NOT import query_service, retriever_node, sql_node, or api … so CI needs no database, model or key."*

**Trade-off:** The AI pipeline itself (LLM calls, ChromaDB retrieval) is not tested. Integration tests do not exist.

**Status:** Accepted

**Files:** `tests/`

**Commit:** `fb67c29`

---

## D-24 · Ruff configured with E9 and F rules only; per-file ignores

**What was decided:** `pyproject.toml` configures ruff with `select = ["E9", "F"]` (syntax errors and pyflakes). Style rules (UP, BLE, RUF, I) are not selected. Several pipeline files have per-file ignores silencing existing violations.

**Reason:** Pyproject comment: *"Only flag real errors … Style/modernisation rules … are intentionally NOT selected so we do not need to touch uneditable pipeline files to pass CI."*

**The per-file ignores and what they hide:**

| File | Ignores | What is hidden |
|---|---|---|
| `sql_validator.py` | F401 | Unused import (`sqlite3` imported but used via string) |
| `sql_executor.py` | F401 | Same |
| `query_service.py` | F401, F821, F841 | Unused imports; possibly undefined names; assigned-but-unused variable (`retrieved_docs`) |
| `eval/run_eval.py` | F541, F841 | f-string without placeholders; assigned-but-unused variable |
| `retriever_node.py` | F401 | Unused import |
| `sql_node.py` | F401 | Unused import |
| `ingest_pipeline.py` | F401 | Unused import |
| `api.py` | F401 | Unused import |

The `F821` ignore on `query_service.py` was added because the initial retry prompt did not import `_allowed_tables_schema`; that import is now present (commit `e01cbc6`), but the ignore remains. This is a **Temporary** entry.

**Status:** Accepted (F401 ignores); Temporary (F821 ignore on `query_service.py` — the name is now defined and the ignore is no longer needed)

**Files:** `pyproject.toml`

**Commit:** `fb67c29`

---

## Decisions I Would Revisit

1. **The few-shot examples** in `ingest_pipeline.py` reference tables (`orders`, `customers`, `products`) that do not exist in the XPTO schema. They could confuse the LLM. They should be replaced with XPTO-specific examples.

2. **No integration test**. The entire LLM-to-SQL path is untested in CI. A mock LLM test would catch prompt regressions.

3. **The F821 ignore** on `query_service.py` is no longer needed now that `_allowed_tables_schema` is imported. It should be removed.

4. **Retry does not handle LLM API failures**. If Groq returns a 429 or 500 during a user query, the error propagates to FastAPI immediately. The eval harness has backoff; the production path does not.

5. **Relaxed grading was chosen post-hoc**. A pre-committed grading rule would make the number more defensible.

---

## Known Gaps

- **NOT BUILT:** CSV/Excel upload, PDF question input, Docker image, live demo URL, token-cost tracking, follow-up questions.
- **PARTIAL:** The retry loop exists but never fired in the benchmark. Its benefit is unmeasured.
- **NOT VERIFIED:** Whether the few-shot examples help or hurt accuracy (no ablation was run).
- **NOT VERIFIED:** Latency on non-Windows hardware.

