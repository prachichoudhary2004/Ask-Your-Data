#!/usr/bin/env python3
"""Main module for the Text-to-SQL Chatbot with RAG application.

This version enriches the ChromaDB vector store with:
  * Row‑level text (original behaviour)
  * Table‑level schema documents (name, description, columns, types, PK/FK, sample values)
  * Column‑level schema documents for fine‑grained retrieval
  * A handful of few‑shot Q/SQL examples for prompt conditioning

The retriever_node keeps using the same Chroma collection interface, so no downstream changes are required.
"""

import os
import sqlite3
import hashlib
import logging
import torch
from tqdm import tqdm
from dotenv import load_dotenv
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_chroma import Chroma

# ---------------------------------------------------------------------------
# Environment & embeddings setup
# ---------------------------------------------------------------------------
load_dotenv()
logging.basicConfig(level=logging.INFO)

DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
logging.info("Using device: %s", DEVICE)

model = HuggingFaceEmbeddings(
    model_name=os.getenv("EMBED_MODEL"), model_kwargs={"device": DEVICE}
)

vectorstore = Chroma(
    collection_name=os.getenv("VECTOR_COLLECTION"),
    embedding_function=model,
    persist_directory=os.getenv("VECTOR_STORE_DIR"),
)

logging.info("Embedding function initialized.")

# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------
def row_hash(values):
    """Generate a stable hash for a SQLite row (used as document ID)."""
    return hashlib.sha256("|".join(map(str, values)).encode()).hexdigest()

def row_to_text(table, cols, row):
    """Convert a SQLite row into a readable text chunk (row document)."""
    return f"Table: {table}\n" + "\n".join([f"{c}: {v}" for c, v in zip(cols, row)])

def _sample_values(conn, table, column, limit: int = 3):
    """Return up to *limit* distinct, non‑null sample values for a column."""
    cur = conn.cursor()
    try:
        cur.execute(
            f"SELECT DISTINCT {column} FROM {table} WHERE {column} IS NOT NULL LIMIT {limit}"
        )
        rows = cur.fetchall()
        return [str(r[0]) for r in rows]
    except Exception:
        return []

def _table_schema_doc(conn, table):
    """Create a comprehensive schema document for *table*."""
    cur = conn.cursor()
    cur.execute(f"PRAGMA table_info({table});")
    cols_info = cur.fetchall()  # cid, name, type, notnull, dflt_value, pk
    columns = [(c[1], c[2], bool(c[5])) for c in cols_info]

    cur.execute(f"PRAGMA foreign_key_list({table});")
    fks = cur.fetchall()  # id, seq, table, from, to, on_update, on_delete, match
    fk_map = {fk[3]: (fk[2], fk[4]) for fk in fks}

    lines = [f"Table: {table}"]
    for name, col_type, is_pk in columns:
        line = f"- Column: {name} ({col_type})"
        if is_pk:
            line += " [PK]"
        if name in fk_map:
            ref_table, ref_col = fk_map[name]
            line += f" [FK -> {ref_table}({ref_col})]"
        samples = _sample_values(conn, table, name, limit=3)
        if samples:
            line += f" | sample values: {', '.join(samples)}"
        lines.append(line)
    doc_text = "\n".join(lines)
    meta = {"type": "table_schema", "table": table}
    return doc_text, meta

def _column_schema_doc(conn, table, column):
    """Create a document describing a single *column* of *table*."""
    cur = conn.cursor()
    cur.execute(f"PRAGMA table_info({table});")
    col_info = [c for c in cur.fetchall() if c[1] == column]
    if not col_info:
        return None, None
    _, name, col_type, _, _, pk = col_info[0]

    cur.execute(f"PRAGMA foreign_key_list({table});")
    fks = cur.fetchall()
    fk_desc = ""
    for fk in fks:
        if fk[3] == name:
            fk_desc = f"Foreign key -> {fk[2]}({fk[4]})"
            break

    samples = _sample_values(conn, table, column, limit=3)
    lines = [f"Column: {name}", f"Table: {table}", f"Type: {col_type}"]
    if pk:
        lines.append("Primary Key: Yes")
    if fk_desc:
        lines.append(fk_desc)
    if samples:
        lines.append(f"Sample values: {', '.join(samples)}")
    doc_text = "\n".join(lines)
    meta = {"type": "column_schema", "table": table, "column": column}
    return doc_text, meta

def _few_shot_examples():
    """Return a small set of Q/SQL few‑shot examples as documents."""
    examples = [
        {
            "question": "How many customers placed orders in 2022?",
            "sql": "SELECT COUNT(DISTINCT customer_id) FROM orders WHERE strftime('%Y', order_date) = '2022'",
        },
        {
            "question": "List the top 5 products by total sales quantity.",
            "sql": "SELECT p.name, SUM(od.quantity) AS total_qty FROM order_details od JOIN products p ON od.product_id = p.id GROUP BY p.id ORDER BY total_qty DESC LIMIT 5",
        },
        {
            "question": "Show the email addresses of customers from Brazil.",
            "sql": "SELECT email FROM customers WHERE country = 'Brazil'",
        },
    ]
    docs, metas = [], []
    for idx, ex in enumerate(examples, 1):
        doc = f"Question: {ex['question']}\nSQL: {ex['sql']}"
        meta = {"type": "few_shot", "example_id": idx}
        docs.append(doc)
        metas.append(meta)
    return docs, metas

# ---------------------------------------------------------------------------
# Indexing functions
# ---------------------------------------------------------------------------
def index_rows(conn, table):
    """Index every row of *table* as a separate document (original behaviour)."""
    cur = conn.cursor()
    cur.execute(f"PRAGMA table_xinfo({table});")
    cols = [c[1] for c in cur.fetchall()]
    cur.execute(f"SELECT {', '.join(cols)} FROM {table}")
    rows = cur.fetchall()

    docs, ids, metas = [], [], []
    for r in rows:
        txt = row_to_text(table, cols, r)
        pk = str(r[0])
        hid = row_hash(r)
        ids.append(f"{table}:{pk}")
        docs.append(txt)
        metas.append({"type": "row", "table": table, "pk": pk, "hash": hid})
    vectorstore.add_texts(texts=docs, metadatas=metas, ids=ids)

def index_schema(conn, table):
    """Index table‑level and column‑level schema documents."""
    # Table document
    table_doc, table_meta = _table_schema_doc(conn, table)
    vectorstore.add_texts(texts=[table_doc], metadatas=[table_meta], ids=[f"table:{table}"])

    # Column documents
    cur = conn.cursor()
    cur.execute(f"PRAGMA table_info({table});")
    columns = [c[1] for c in cur.fetchall()]
    col_docs, col_metas, col_ids = [], [], []
    for col in columns:
        doc, meta = _column_schema_doc(conn, table, col)
        if doc:
            col_docs.append(doc)
            col_metas.append(meta)
            col_ids.append(f"column:{table}:{col}")
    if col_docs:
        vectorstore.add_texts(texts=col_docs, metadatas=col_metas, ids=col_ids)

def index_few_shot():
    docs, metas = _few_shot_examples()
    ids = [f"fewshot:{i}" for i in range(len(docs))]
    vectorstore.add_texts(texts=docs, metadatas=metas, ids=ids)

# ---------------------------------------------------------------------------
# Full indexing pipeline
# ---------------------------------------------------------------------------
def main():
    """Run the complete ingestion pipeline.

    The pipeline now indexes four kinds of documents:
        1. Row‑level text (unchanged)
        2. Table‑level schema documentation
        3. Column‑level schema documentation
        4. Few‑shot Q/SQL example documents
    """
    logging.info("index_tables: Starting indexing process.")
    conn = sqlite3.connect(os.getenv("DATABASE_PATH"))
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
    tables = [t[0] for t in cur.fetchall()]
    logging.info("Indexing %d tables.", len(tables))

    for table in tqdm(tables, desc="Indexing tables"):
        index_rows(conn, table)
        index_schema(conn, table)

    # Add few‑shot examples once
    index_few_shot()

    conn.close()
    logging.info("Indexing complete and persisted in Chroma.")

if __name__ == "__main__":
    main()
    logging.info("index_tables: Exiting.")

"""Main module for the Text-to-SQL Chatbot with RAG application.

This version enriches the ChromaDB vector store with:
  * Row‑level text (original behaviour)
  * Table‑level schema documents (name, description, columns, types, PK/FK, sample values)
  * Column‑level schema documents for fine‑grained retrieval
  * A handful of few‑shot Q/SQL examples for prompt conditioning

The retriever_node keeps using the same Chroma collection interface, so no downstream changes are required.
"""

import os
import sqlite3
import hashlib
import logging
import torch
from tqdm import tqdm
from dotenv import load_dotenv
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_chroma import Chroma

# ---------------------------------------------------------------------------
# Environment & embeddings setup
# ---------------------------------------------------------------------------
load_dotenv()
logging.basicConfig(level=logging.INFO)

DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
logging.info("Using device: %s", DEVICE)

model = HuggingFaceEmbeddings(
    model_name=os.getenv("EMBED_MODEL"), model_kwargs={"device": DEVICE}
)

vectorstore = Chroma(
    collection_name=os.getenv("VECTOR_COLLECTION"),
    embedding_function=model,
    persist_directory=os.getenv("VECTOR_STORE_DIR"),
)

logging.info("Embedding function initialized.")

# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------
def row_hash(values):
    """Generate a stable hash for a SQLite row (used as document ID)."""
    return hashlib.sha256("|".join(map(str, values)).encode()).hexdigest()

def row_to_text(table, cols, row):
    """Convert a SQLite row into a readable text chunk (row document)."""
    return f"Table: {table}\n" + "\n".join([f"{c}: {v}" for c, v in zip(cols, row)])

def _sample_values(conn, table, column, limit: int = 3):
    """Return up to *limit* distinct, non‑null sample values for a column.

    Sample values help the LLM understand the data distribution and are included
    in the schema documents.
    """
    cur = conn.cursor()
    try:
        cur.execute(f"SELECT DISTINCT {column} FROM {table} WHERE {column} IS NOT NULL LIMIT {limit}")
        rows = cur.fetchall()
        return [str(r[0]) for r in rows]
    except Exception:
        return []

def _table_schema_doc(conn, table):
    """Create a comprehensive schema document for *table*.

    Includes column names, types, primary/foreign‑key information, and three
    sample values per column.
    """
    cur = conn.cursor()
    # Columns + types + pk flag
    cur.execute(f"PRAGMA table_info({table});")
    cols_info = cur.fetchall()  # cid, name, type, notnull, dflt_value, pk
    columns = [(c[1], c[2], bool(c[5])) for c in cols_info]
    # Foreign keys
    cur.execute(f"PRAGMA foreign_key_list({table});")
    fks = cur.fetchall()  # id, seq, table, from, to, on_update, on_delete, match
    fk_map = {fk[3]: (fk[2], fk[4]) for fk in fks}
    # Build description lines
    lines = [f"Table: {table}"]
    for name, col_type, is_pk in columns:
        line = f"- Column: {name} ({col_type})"
        if is_pk:
            line += " [PK]"
        if name in fk_map:
            ref_table, ref_col = fk_map[name]
            line += f" [FK -> {ref_table}({ref_col})]"
        samples = _sample_values(conn, table, name, limit=3)
        if samples:
            line += f" | sample values: {', '.join(samples)}"
        lines.append(line)
    doc_text = "\n".join(lines)
    meta = {"type": "table_schema", "table": table}
    return doc_text, meta

def _column_schema_doc(conn, table, column):
    """Create a document describing a single *column* of *table*.

    The document contains type, PK/FK flags and sample values, enabling very
    granular retrieval when the user asks about a specific column.
    """
    cur = conn.cursor()
    cur.execute(f"PRAGMA table_info({table});")
    col_info = [c for c in cur.fetchall() if c[1] == column]
    if not col_info:
        return None, None
    _, name, col_type, _, _, pk = col_info[0]
    # Foreign key detection
    cur.execute(f"PRAGMA foreign_key_list({table});")
    fks = cur.fetchall()
    fk_desc = ""
    for fk in fks:
        if fk[3] == name:
            fk_desc = f"Foreign key -> {fk[2]}({fk[4]})"
            break
    samples = _sample_values(conn, table, column, limit=3)
    lines = [f"Column: {name}", f"Table: {table}", f"Type: {col_type}"]
    if pk:
        lines.append("Primary Key: Yes")
    if fk_desc:
        lines.append(fk_desc)
    if samples:
        lines.append(f"Sample values: {', '.join(samples)}")
    doc_text = "\n".join(lines)
    meta = {"type": "column_schema", "table": table, "column": column}
    return doc_text, meta

def _few_shot_examples():
    """Return a small set of Q/SQL few‑shot examples as documents.

    These are added to the vector store so the prompt can retrieve them alongside
    schema information.
    """
    examples = [
        {
            "question": "How many customers placed orders in 2022?",
            "sql": "SELECT COUNT(DISTINCT customer_id) FROM orders WHERE strftime('%Y', order_date) = '2022'",
        },
        {
            "question": "List the top 5 products by total sales quantity.",
            "sql": "SELECT p.name, SUM(od.quantity) AS total_qty FROM order_details od JOIN products p ON od.product_id = p.id GROUP BY p.id ORDER BY total_qty DESC LIMIT 5",
        },
        {
            "question": "Show the email addresses of customers from Brazil.",
            "sql": "SELECT email FROM customers WHERE country = 'Brazil'",
        },
    ]
    docs = []
    metas = []
    for idx, ex in enumerate(examples, 1):
        doc = f"Question: {ex['question']}\nSQL: {ex['sql']}"
        meta = {"type": "few_shot", "example_id": idx}
        docs.append(doc)
        metas.append(meta)
    return docs, metas

# ---------------------------------------------------------------------------
# Row indexing (original behaviour)
# ---------------------------------------------------------------------------
def index_table(conn, table):
    """Index every row of *table* as a separate document.

    The original implementation is retained so the system can still retrieve
    concrete row values when necessary.
    """
    cur = conn.cursor()
    cur.execute(f"PRAGMA table_xinfo({table});")
    cols = [c[1] for c in cur.fetchall()]
    cur.execute(f"SELECT {', '.join(cols)} FROM {table}")
    rows = cur.fetchall()

    docs, ids, metas = [], [], []
    for r in rows:
        txt = row_to_text(table, cols, r)
        pk = str(r[0])
        hid = row_hash(r)
        ids.append(f"{table}:{pk}")
        docs.append(txt)
        metas.append({"type": "row", "table": table, "pk": pk, "hash": hid})
    vectorstore.add_texts(texts=docs, metadatas=metas, ids=ids)

# ---------------------------------------------------------------------------
# Full indexing pipeline
# ---------------------------------------------------------------------------
def main():
    """Run the complete ingestion pipeline.

    The pipeline now indexes four kinds of documents:
        1. Row‑level text (unchanged)
        2. Table‑level schema documentation
        3. Column‑level schema documentation
        4. Few‑shot Q/SQL example documents
    """
    logging.info("index_tables: Starting indexing process.")
    conn = sqlite3.connect(os.getenv("DATABASE_PATH"))
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
    tables = [t[0] for t in cur.fetchall()]
    logging.info("Indexing %d tables.", len(tables))

    # 1️⃣ Row documents (original behaviour)
    for table in tqdm(tables, desc="Indexing rows"):
        index_table(conn, table)

    # 2️⃣ Table‑level schema documents
    tbl_docs, tbl_ids, tbl_metas = [], [], []
    for table in tables:
        doc, meta = _table_schema_doc(conn, table)
        tbl_docs.append(doc)
        tbl_ids.append(f"schema:table:{table}")
        tbl_metas.append(meta)
    if tbl_docs:
        vectorstore.add_texts(texts=tbl_docs, metadatas=tbl_metas, ids=tbl_ids)

    # 3️⃣ Column‑level schema documents
    col_docs, col_ids, col_metas = [], [], []
    for table in tables:
        cur.execute(f"PRAGMA table_info({table});")
        columns = [c[1] for c in cur.fetchall()]
        for col in columns:
            doc, meta = _column_schema_doc(conn, table, col)
            if doc:
                col_docs.append(doc)
                col_ids.append(f"schema:col:{table}:{col}")
                col_metas.append(meta)
    if col_docs:
        vectorstore.add_texts(texts=col_docs, metadatas=col_metas, ids=col_ids)

    # 4️⃣ Few‑shot examples
    few_docs, few_metas = _few_shot_examples()
    if few_docs:
        ids = [f"fewshot:{i}" for i in range(1, len(few_docs) + 1)]
        vectorstore.add_texts(texts=few_docs, metadatas=few_metas, ids=ids)

    conn.close()
    logging.info("Indexing complete and persisted in Chroma.")

if __name__ == "__main__":
    main()
    logging.info("index_tables: Exiting.")

"""Main module for the Text-to-SQL Chatbot with RAG application.

This version enriches the ChromaDB vector store with:
  * Row‑level text (original behaviour)
  * Table‑level schema documents (name, description, columns, types, PK/FK, sample values)
  * Column‑level schema documents for fine‑grained retrieval
  * A handful of few‑shot Q/SQL examples for prompt conditioning

The retriever_node keeps using the same Chroma collection interface, so no downstream changes are required.
"""

import os
import sqlite3
import hashlib
import logging
import torch
from tqdm import tqdm
from dotenv import load_dotenv
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_chroma import Chroma

# ---------------------------------------------------------------------------
# Environment & embeddings setup
# ---------------------------------------------------------------------------
load_dotenv()
logging.basicConfig(level=logging.INFO)

DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
logging.info("Using device: %s", DEVICE)

model = HuggingFaceEmbeddings(
    model_name=os.getenv("EMBED_MODEL"), model_kwargs={"device": DEVICE}
)

vectorstore = Chroma(
    collection_name=os.getenv("VECTOR_COLLECTION"),
    embedding_function=model,
    persist_directory=os.getenv("VECTOR_STORE_DIR"),
)

logging.info("Embedding function initialized.")

# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------
def row_hash(values):
    """Generate a stable hash for a SQLite row (used as document ID)."""
    return hashlib.sha256("|".join(map(str, values)).encode()).hexdigest()

def row_to_text(table, cols, row):
    """Convert a SQLite row into a readable text chunk (row document)."""
    return f"Table: {table}\n" + "\n".join([f"{c}: {v}" for c, v in zip(cols, row)])

def _sample_values(conn, table, column, limit: int = 3):
    """Return up to *limit* distinct, non‑null sample values for a column.

    Sample values help the LLM understand the data distribution and are included
    in the schema documents.
    """
    cur = conn.cursor()
    try:
        cur.execute(f"SELECT DISTINCT {column} FROM {table} WHERE {column} IS NOT NULL LIMIT {limit}")
        rows = cur.fetchall()
        return [str(r[0]) for r in rows]
    except Exception:
        return []

def _table_schema_doc(conn, table):
    """Create a comprehensive schema document for *table*.

    Includes column names, types, primary/foreign‑key information, and three
    sample values per column.
    """
    cur = conn.cursor()
    # Columns + types + pk flag
    cur.execute(f"PRAGMA table_info({table});")
    cols_info = cur.fetchall()  # cid, name, type, notnull, dflt_value, pk
    columns = [(c[1], c[2], bool(c[5])) for c in cols_info]
    # Foreign keys
    cur.execute(f"PRAGMA foreign_key_list({table});")
    fks = cur.fetchall()  # id, seq, table, from, to, on_update, on_delete, match
    fk_map = {fk[3]: (fk[2], fk[4]) for fk in fks}
    # Build description lines
    lines = [f"Table: {table}"]
    for name, col_type, is_pk in columns:
        line = f"- Column: {name} ({col_type})"
        if is_pk:
            line += " [PK]"
        if name in fk_map:
            ref_table, ref_col = fk_map[name]
            line += f" [FK -> {ref_table}({ref_col})]"
        samples = _sample_values(conn, table, name, limit=3)
        if samples:
            line += f" | sample values: {', '.join(samples)}"
        lines.append(line)
    doc_text = "\n".join(lines)
    meta = {"type": "table_schema", "table": table}
    return doc_text, meta

def _column_schema_doc(conn, table, column):
    """Create a document describing a single *column* of *table*.

    The document contains type, PK/FK flags and sample values, enabling very
    granular retrieval when the user asks about a specific column.
    """
    cur = conn.cursor()
    cur.execute(f"PRAGMA table_info({table});")
    col_info = [c for c in cur.fetchall() if c[1] == column]
    if not col_info:
        return None, None
    _, name, col_type, _, _, pk = col_info[0]
    # Foreign key detection
    cur.execute(f"PRAGMA foreign_key_list({table});")
    fks = cur.fetchall()
    fk_desc = ""
    for fk in fks:
        if fk[3] == name:
            fk_desc = f"Foreign key -> {fk[2]}({fk[4]})"
            break
    samples = _sample_values(conn, table, column, limit=3)
    lines = [f"Column: {name}", f"Table: {table}", f"Type: {col_type}"]
    if pk:
        lines.append("Primary Key: Yes")
    if fk_desc:
        lines.append(fk_desc)
    if samples:
        lines.append(f"Sample values: {', '.join(samples)}")
    doc_text = "\n".join(lines)
    meta = {"type": "column_schema", "table": table, "column": column}
    return doc_text, meta

def _few_shot_examples():
    """Return a small set of Q/SQL few‑shot examples as documents.

    These are added to the vector store so the prompt can retrieve them alongside
    schema information.
    """
    examples = [
        {
            "question": "How many customers placed orders in 2022?",
            "sql": "SELECT COUNT(DISTINCT customer_id) FROM orders WHERE strftime('%Y', order_date) = '2022'",
        },
        {
            "question": "List the top 5 products by total sales quantity.",
            "sql": "SELECT p.name, SUM(od.quantity) AS total_qty FROM order_details od JOIN products p ON od.product_id = p.id GROUP BY p.id ORDER BY total_qty DESC LIMIT 5",
        },
        {
            "question": "Show the email addresses of customers from Brazil.",
            "sql": "SELECT email FROM customers WHERE country = 'Brazil'",
        },
    ]
    docs = []
    metas = []
    for idx, ex in enumerate(examples, 1):
        doc = f"Question: {ex['question']}\nSQL: {ex['sql']}"
        meta = {"type": "few_shot", "example_id": idx}
        docs.append(doc)
        metas.append(meta)
    return docs, metas

# ---------------------------------------------------------------------------
# Row indexing (original behaviour)
# ---------------------------------------------------------------------------
def index_table(conn, table):
    """Index every row of *table* as a separate document.

    The original implementation is retained so the system can still retrieve
    concrete row values when necessary.
    """
    cur = conn.cursor()
    cur.execute(f"PRAGMA table_xinfo({table});")
    cols = [c[1] for c in cur.fetchall()]
    cur.execute(f"SELECT {', '.join(cols)} FROM {table}")
    rows = cur.fetchall()

    docs, ids, metas = [], [], []
    for r in rows:
        txt = row_to_text(table, cols, r)
        pk = str(r[0])
        hid = row_hash(r)
        ids.append(f"{table}:{pk}")
        docs.append(txt)
        metas.append({"type": "row", "table": table, "pk": pk, "hash": hid})
    vectorstore.add_texts(texts=docs, metadatas=metas, ids=ids)

# ---------------------------------------------------------------------------
# Full indexing pipeline
# ---------------------------------------------------------------------------
def main():
    """Run the complete ingestion pipeline.

    The pipeline now indexes four kinds of documents:
        1. Row‑level text (unchanged)
        2. Table‑level schema documentation
        3. Column‑level schema documentation
        4. Few‑shot Q/SQL example documents
    """
    logging.info("index_tables: Starting indexing process.")
    conn = sqlite3.connect(os.getenv("DATABASE_PATH"))
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
    tables = [t[0] for t in cur.fetchall()]
    logging.info("Indexing %d tables.", len(tables))

    # 1️⃣ Row documents (original behaviour)
    for table in tqdm(tables, desc="Indexing rows"):
        index_table(conn, table)

    # 2️⃣ Table‑level schema documents
    tbl_docs, tbl_ids, tbl_metas = [], [], []
    for table in tables:
        doc, meta = _table_schema_doc(conn, table)
        tbl_docs.append(doc)
        tbl_ids.append(f"schema:table:{table}")
        tbl_metas.append(meta)
    if tbl_docs:
        vectorstore.add_texts(texts=tbl_docs, metadatas=tbl_metas, ids=tbl_ids)

    # 3️⃣ Column‑level schema documents
    col_docs, col_ids, col_metas = [], [], []
    for table in tables:
        cur.execute(f"PRAGMA table_info({table});")
        columns = [c[1] for c in cur.fetchall()]
        for col in columns:
            doc, meta = _column_schema_doc(conn, table, col)
            if doc:
                col_docs.append(doc)
                col_ids.append(f"schema:col:{table}:{col}")
                col_metas.append(meta)
    if col_docs:
        vectorstore.add_texts(texts=col_docs, metadatas=col_metas, ids=col_ids)

    # 4️⃣ Few‑shot examples
    few_docs, few_metas = _few_shot_examples()
    if few_docs:
        ids = [f"fewshot:{i}" for i in range(1, len(few_docs) + 1)]
        vectorstore.add_texts(texts=few_docs, metadatas=few_metas, ids=ids)

    conn.close()
    logging.info("Indexing complete and persisted in Chroma.")

if __name__ == "__main__":
    main()
    logging.info("index_tables: Exiting.")
