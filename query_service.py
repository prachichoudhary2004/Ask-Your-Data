'''Query service module for the Text-to-SQL Chatbot with RAG application.
'''
import os
import logging
import re
from pydantic import BaseModel

from retriever_node import retriever_node
from sql_node import sql_generator_node
from sql_validator import validate_sql, allowed_tables_from_db
from sql_executor import execute_sql

# LLM imports for retry generation
from dotenv import load_dotenv
from langchain_groq import ChatGroq

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

ALLOWED_TABLES = allowed_tables_from_db(os.getenv("DATABASE_PATH"))


class QueryRequest(BaseModel):
    """Query request"""
    question: str
    show_sql: bool = True


def _llm_retry(question: str, failed_sql: str, error_msg: str) -> str:
    """Call the LLM to get a corrected SELECT statement.

    The prompt includes the original question, the previously generated SQL,
    and the exact error message. It asks the model to return only a corrected
    SELECT statement using the allowed tables/columns.
    """
    llm = ChatGroq(
        model=os.getenv("LLM_MODEL"),
        temperature=float(os.getenv("LLM_TEMPERATURE", "0")),
        api_key=os.getenv("LLM_API_KEY"),
        max_tokens=1024,
    )
    prompt = (
        "You are a SQL generator. The previous attempt produced invalid SQL."
        " Please fix it based on the original question and the error."
        f"\n\nOriginal question: {question}"
        f"\nFailed SQL: {failed_sql}"
        f"\nError: {error_msg}"
        "\n\nReturn ONLY the corrected SELECT statement (no markdown, no semicolons)."
    )
    out = llm.invoke(prompt)
    if hasattr(out, "content"):
        out = out.content
    # Strip code fences and whitespace
    out = re.sub(r"```(?:sql)?\n?", "", str(out), flags=re.IGNORECASE).replace("```", "").strip()
    # Ensure we have a SELECT
    match = re.search(r"(select\b.*)", out, flags=re.IGNORECASE | re.DOTALL)
    if match:
        out = match.group(1).strip()
    return out.rstrip(";").strip()


def query(req: QueryRequest):
    """Process a query request through the RAG pipeline with up to 2 retries.

    Returns a dict containing the generated SQL (if requested), column names,
    rows, and the number of attempts made.
    """
    logger.info("Processing query request: %s", req.question)
    attempts = 0
    max_attempts = 3  # initial try + up to 2 retries

    # 1. retrieve once – reuse the same context for retries
    state = {"question": req.question, "messages": []}
    state = retriever_node(state)
    # keep retrieved docs for possible reuse
    retrieved_docs = state.get("retrieved_docs", [])
    error_msg = ""
    while attempts < max_attempts:
        attempts += 1
        logger.info("Attempt %d for question: %s", attempts, req.question)

        if attempts == 1:
            # first attempt – normal generation
            state = sql_generator_node(state)
        else:
            # subsequent attempts – use LLM retry helper
            prev_sql = state.get("generated_sql", "")
            # The error message is stored in `error_msg` variable from previous loop
            state["generated_sql"] = _llm_retry(req.question, prev_sql, error_msg)

        sql = state["generated_sql"]

        # 3. validate SQL before executing
        ok, reason = validate_sql(sql, ALLOWED_TABLES)
        if not ok:
            logger.error("Generated SQL validation failed: %s", reason)
            error_msg = reason
            if attempts >= max_attempts:
                raise ValueError(f"Generated SQL failed validation after {attempts} attempts: {reason}")
            continue

        # 4. execute
        try:
            cols, rows = execute_sql(sql)
        except Exception as e:
            logger.error("SQL execution error on attempt %d: %s", attempts, e)
            error_msg = str(e)
            if attempts >= max_attempts:
                raise RuntimeError(f"SQL execution failed after {attempts} attempts: {e}")
            continue

        # success – break the loop
        result_rows = [dict(zip(cols, r)) for r in rows]
        return {
            "sql": sql if req.show_sql else None,
            "cols": cols,
            "rows": result_rows,
            "attempts": attempts,
        }

    # Should never reach here because loop either returns or raises
    raise RuntimeError("Unexpected exit from retry loop")

# print(query(QueryRequest(question="how many sales were made in 2024?")))
