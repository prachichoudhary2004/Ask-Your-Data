#!/usr/bin/env python3
"""SQL generation node for the Text-to-SQL RAG chatbot.

The prompt now receives:
  * The retrieved documents (as before)
  * A full list of *allowed* tables and their columns (generated from the
    SQLite schema at runtime)
  * An explicit instruction: "use only these tables and columns."

This reduces hallucinations such as referencing tables that do not exist.
"""

import os
import re
import sqlite3
from dotenv import load_dotenv
from langchain_core.prompts import PromptTemplate
from langchain_groq import ChatGroq

from retriever_node import RAGState

load_dotenv()

# ---------------------------------------------------------------------------
# LLM helper
# ---------------------------------------------------------------------------
def _get_llm() -> ChatGroq:
    """Instantiate the Groq LLM using environment variables.

    The function is called on each generation so changes to ``.env`` are picked
    up without restarting the process.
    """
    return ChatGroq(
        model=os.getenv("LLM_MODEL"),
        temperature=float(os.getenv("LLM_TEMPERATURE", "0")),
        api_key=os.getenv("LLM_API_KEY"),
        max_tokens=1024,
    )

# ---------------------------------------------------------------------------
# Schema extraction
# ---------------------------------------------------------------------------
def _allowed_tables_schema() -> str:
    """Return a formatted string describing every table and its columns.

    Example output:
        Table: customers (id INTEGER PK, name TEXT, country TEXT)
        Table: orders (order_id INTEGER PK, customer_id INTEGER FK -> customers(id), order_date TEXT)
    The string is inserted into the LLM prompt so the model only sees tables that
    actually exist in the database.
    """
    db_path = os.getenv("DATABASE_PATH")
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
    tables = [t[0] for t in cur.fetchall()]
    lines = []
    for table in tables:
        cur.execute(f"PRAGMA table_info({table});")
        cols = cur.fetchall()  # cid, name, type, notnull, dflt_value, pk
        col_parts = []
        for _, col_name, col_type, _, _, pk in cols:
            part = f"{col_name} {col_type}"
            if pk:
                part += " PK"
            col_parts.append(part)
        # Foreign keys – optional, add as comments for clarity
        cur.execute(f"PRAGMA foreign_key_list({table});")
        fks = cur.fetchall()
        fk_parts = []
        for _, _, ref_table, from_col, to_col, *_ in fks:
            fk_parts.append(f"{from_col} -> {ref_table}({to_col})")
        if fk_parts:
            col_parts.append("FK: " + ", ".join(fk_parts))
        line = f"Table: {table} (" + ", ".join(col_parts) + ")"
        lines.append(line)
    conn.close()
    return "\n".join(lines)

# ---------------------------------------------------------------------------
# Prompt template – now includes the allowed schema and a strict usage rule
# ---------------------------------------------------------------------------
sql_agent_prompt = PromptTemplate.from_template(
    """You are a SQL generator. Based on the following context, generate a SINGLE READ-ONLY SQLite SELECT query (no semicolons, no multiple statements).\n\n"
    "Context:\n{context}\n\n"
    "Allowed tables and columns (use *only* these):\n{allowed_schema}\n\n"
    "Question:\n{question}\n\n"
    "Rules:\n- Only generate SQL SELECT statements.\n- Use only the tables and columns listed above.\n- Return only the SQL SELECT statement.\n"""
)

# ---------------------------------------------------------------------------
# Main node
# ---------------------------------------------------------------------------
def sql_generator_node(state: RAGState) -> RAGState:
    """Generate SQL from retrieved documents and the user question.

    The function:
      1. Joins retrieved documents into a single context string.
      2. Retrieves the allowed‑tables schema string.
      3. Formats the prompt and calls the LLM.
      4. Cleans the output, ensuring it is a single SELECT statement.
    """
    # 1. Combine retrieved documents
    context = "\n\n".join(state.get("retrieved_docs", []))

    # 2. Allowed schema string
    allowed_schema = _allowed_tables_schema()

    # 3. Build the prompt
    prompt_text = sql_agent_prompt.format(
        context=context, allowed_schema=allowed_schema, question=state["question"]
    )

    # 4. Call the LLM
    out = _get_llm().invoke(prompt_text)
    if hasattr(out, "content"):
        out = out.content
    out = str(out).strip()

    # 5. Strip code fences and surrounding whitespace
    out = re.sub(r"```(?:sql)?\n?", "", out, flags=re.IGNORECASE).replace("```", "").strip()

    # 6. Extract the SELECT statement (case‑insensitive)
    match = re.search(r"(select\\b.*)", out, flags=re.IGNORECASE | re.DOTALL)
    if match:
        out = match.group(1).strip()
    else:
        out = ""

    # 7. Remove a trailing semicolon if present
    out = out.rstrip(";").strip()

    # 8. Store the cleaned SQL back into the state
    state["generated_sql"] = out
    return state

# Example usage (for quick debugging):
# if __name__ == "__main__":
#     sample_state = {"question": "List customers with their orders", "retrieved_docs": []}
#     print(sql_generator_node(sample_state)["generated_sql"])

import re
import os

from langchain_core.prompts import PromptTemplate
from dotenv import load_dotenv
from langchain_groq import ChatGroq

from retriever_node import RAGState

load_dotenv()


def _get_llm() -> ChatGroq:
    """Build LLM from current env vars (re-reads on each call so .env changes take effect)."""
    return ChatGroq(
        model=os.getenv("LLM_MODEL"),
        temperature=float(os.getenv("LLM_TEMPERATURE", "0")),
        api_key=os.getenv("LLM_API_KEY"),
        max_tokens=1024,
    )



sql_agent_prompt = PromptTemplate.from_template("""
        You are a SQL generator. Based on the following context, generate a SINGLE READ-ONLY SQLite SELECT query (no semicolons, no multiple statements).
        Context:
        {context}
        
        Question:
        {question}
        
        Rules:
        - Only generate SQL SELECT statements.
        - Return only the SQL SELECT statement.                                                
        """)

def sql_generator_node(state: RAGState) -> RAGState:
    """
    Generate SQL from the retrieved documents and user question.
    Cleans LLM output, removes markdown/code fences, and ensures only SELECT statements remain.
    """
    # 1. Combine retrieved documents
    context = "\n\n".join(state.get("retrieved_docs", []))

    # 2. Format the prompt
    prompt_text = sql_agent_prompt.format(context=context, question=state["question"])

    # 3. Call the LLM
    out = _get_llm().invoke(prompt_text)

    # 4. Extract text content if output is an AIMessage or ChatResult
    if hasattr(out, "content"):
        out = out.content
    out = str(out).strip()

    # 5. Remove code fences ``` or ```sql and any leading/trailing whitespace
    out = re.sub(r"```(?:sql)?\n?", "", out, flags=re.IGNORECASE).replace("```", "").strip()

    # 6. Ensure the SQL starts with SELECT (case-insensitive)
    match = re.search(r"(select\b.*)", out, flags=re.IGNORECASE | re.DOTALL)
    if match:
        out = match.group(1).strip()
    else:
        # fallback if no SELECT found
        out = ""

    # 7. Optional: remove trailing semicolon if present
    out = out.rstrip(";").strip()

    # 8. Save cleaned SQL back to state
    state["generated_sql"] = out
    return state


# print(sql_generator_node({"question": "phone number of ABC Commerce Inc"}))
