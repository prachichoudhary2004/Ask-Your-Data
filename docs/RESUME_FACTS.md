# Resume Fact Sheet — Ask Your Data

A one-page reference for interviews and job applications. Every claim here is verified against the code or the eval results files.

---

## Project Name

**Ask Your Data** — Text-to-SQL Chatbot with RAG

GitHub: [https://github.com/prachichoudhary2004/Ask-Your-Data](https://github.com/prachichoudhary2004/Ask-Your-Data)

---

## One Line

Built a RAG pipeline that turns plain-English (and Portuguese) questions into SQL, validates and executes them against a SQLite database, and displays results with automatic charts in a Streamlit chat UI.

---

## Tech Stack Actually Used

Python 3.13 · LangChain · LangChain-Groq · ChromaDB · sentence-transformers (local) · cross-encoder (local) · FastAPI · Uvicorn · Streamlit · SQLite · sqlglot · pandas · pytest · ruff · GitHub Actions · uv

---

## WORKING Features (with Evidence)

| Claim | Evidence |
|---|---|
| Converts natural-language questions to SQL | `sql_node.py::sql_generator_node` — formats a prompt with retrieved context + full schema, calls Groq LLM |
| Schema-aware prompting (LLM sees all tables and columns) | `sql_node.py::_allowed_tables_schema` — queries SQLite PRAGMA at runtime; result injected into every prompt (commit `d2f31d2`) |
| RAG retrieval with cross-encoder reranking | `retriever_node.py::retriever_node` retrieves TOP_K×3, `retriever_node.py::rerank_documents` reranks and keeps TOP_K |
| SQL safety validation (SELECT-only, table allowlist) | `sql_validator.py::validate_sql` — keyword blocklist + sqlglot parse + table allowlist |
| Read-only database access | `sql_executor.py::open_ro_conn` — `?mode=ro` SQLite URI |
| Retry loop on validation/execution failure | `query_service.py::query` — up to 3 attempts; `query_service.py::_llm_retry` called on failure |
| Retry prompt includes the schema | `query_service.py::_llm_retry` calls `_allowed_tables_schema()` (commit `e01cbc6`) |
| Automatic chart selection (bar / line / none) | `chart_utils.py::choose_chart` — 3-rule heuristic based on column types and row count |
| Chat history with Streamlit session state | `ui.py::main` — `st.session_state.messages` stores and replays all turns |
| Download results as CSV | `ui.py::render_answer` — `st.download_button` with `df.to_csv()` |
| FastAPI REST endpoint | `api.py::process_query` — POST `/query`, returns latency and attempt count |
| 22 unit tests | `tests/test_validator.py` (validator), `tests/test_executor.py` (executor), `tests/test_chart_utils.py` (chart logic) |
| GitHub Actions CI | `.github/workflows/ci.yml` — runs `ruff check` and `pytest` on every push |
| Four ChromaDB document types | `ingest_pipeline.py::main` — indexes rows, table schemas, column schemas, few-shot examples |

---

## PARTIAL Features

| Feature | What works | What does not |
|---|---|---|
| Retry on Groq rate-limit (429) | `eval/run_eval.py` has exponential backoff for the eval harness | The user-facing API (`api.py` / `query_service.py`) does not handle 429; the exception propagates |
| Retry loop effectiveness | The loop is implemented and tested by unit test | It never ran during the benchmark; its benefit is unmeasured |

---

## NOT BUILT

CSV / Excel upload · PDF question input · Docker image · live demo URL · token-cost tracking · follow-up / multi-turn SQL refinement

---

## Verified Numbers

All numbers below come from `eval/results/results-20261029T192658Z.json` and `eval/results/regrade-20261029T193440Z.json`.

| Metric | Value | What it means | Limits |
|---|---|---|---|
| RELAXED accuracy | **85% (34/40)** | The pipeline returned the right row values, ignoring column aliases and column order | The RELAXED rule was defined **after** seeing the original results. It inflates the reported score. |
| STRICT accuracy | **22.5% (9/40)** | Column names AND row values both match the gold SQL | Strict penalises reasonable alias choices (e.g. naming a count `total` vs `total_sales`) |
| Original harness | **52.5% (21/40)** | Row values only (the eval harness comparison); not a published benchmark | The harness ignores column names, so it is between strict and relaxed |
| Questions evaluated | **40** | 10 Portuguese, 5 marked "tricky" | Single domain (one fictional Brazilian company) |
| Automated tests | **22** | Validator, executor, chart helper | No integration tests; LLM pipeline is not tested |
| Average latency | **7.60 s** | Mean over all 40 questions, measured end-to-end in `eval/run_eval.py` | Includes model loading on first call; later calls are faster |
| Model at eval time | `openai/gpt-oss-120b` on Groq | Run 2026-10-29 | The project started on `llama-3.3-70b-versatile`. The switch to `gpt-oss-120b` was most likely made by an AI coding-agent session, not as a deliberate human decision. No commit records this change. |
| Eval date | 2026-10-29 | — | One run only |

---

## Claims I Must NOT Make

- Do not say the 85% number is directly comparable to any published Text-to-SQL benchmark (Spider, WikiSQL, BIRD, etc.). It is not. The dataset is private, domain-specific, and the scoring rule was chosen after seeing the results.
- Do not say the retry loop "improved" accuracy. It never ran in the benchmark. There is no measured improvement.
- Do not say the system generalises to schemas other than XPTO Empresa. This has not been tested.
- Do not say the strict score is 85%. It is 22.5%. Always specify which scoring rule you are citing.
- Do not claim a "live demo" exists. None has been deployed.
- Do not claim Docker or containerisation. No Dockerfile exists.
- Do not say the model switch to `gpt-oss-120b` was a deliberate design choice. It was most likely made automatically.

