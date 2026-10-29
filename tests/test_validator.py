"""tests/test_validator.py - Unit tests for sql_validator.py.

Does NOT import query_service, retriever_node, sql_node, or api.
All tests that exercise allowed_tables_from_db use a temporary SQLite file.
"""
import os
import sqlite3
import tempfile

from sql_validator import allowed_tables_from_db, validate_sql

# A fixed set of allowed tables used in most tests.
ALLOWED = {"clientes", "produtos", "vendas", "funcionarios"}


# ---------------------------------------------------------------------------
# validate_sql - happy path
# ---------------------------------------------------------------------------
def test_valid_select_passes():
    ok, msg = validate_sql("SELECT id, nome FROM clientes", ALLOWED)
    assert ok, msg


# ---------------------------------------------------------------------------
# validate_sql - disallowed DML / DDL keywords
# ---------------------------------------------------------------------------
def test_drop_is_rejected():
    ok, _msg = validate_sql("drop table clientes", ALLOWED)
    assert not ok


def test_update_is_rejected():
    ok, _msg = validate_sql("update clientes set nome='x' where id=1", ALLOWED)
    assert not ok


def test_delete_is_rejected():
    ok, _msg = validate_sql("delete from clientes where id=1", ALLOWED)
    assert not ok


def test_insert_is_rejected():
    ok, _msg = validate_sql("insert into clientes (nome) values ('x')", ALLOWED)
    assert not ok


# ---------------------------------------------------------------------------
# validate_sql - semicolon (multiple statements)
# ---------------------------------------------------------------------------
def test_semicolon_is_rejected():
    ok, msg = validate_sql("SELECT 1; DROP TABLE clientes", ALLOWED)
    assert not ok
    assert "semicolon" in msg


# ---------------------------------------------------------------------------
# validate_sql - unknown table
# ---------------------------------------------------------------------------
def test_unknown_table_rejected():
    ok, msg = validate_sql("SELECT * FROM orders", ALLOWED)
    assert not ok
    assert "orders" in msg or "disallowed" in msg


# ---------------------------------------------------------------------------
# validate_sql - metadata tables are not in ALLOWED so they are rejected
# ---------------------------------------------------------------------------
def test_table_comments_rejected():
    ok, _msg = validate_sql("SELECT * FROM table_comments", ALLOWED)
    assert not ok


def test_column_comments_rejected():
    ok, _msg = validate_sql("SELECT * FROM column_comments", ALLOWED)
    assert not ok


# ---------------------------------------------------------------------------
# allowed_tables_from_db - returns only business tables
# ---------------------------------------------------------------------------
def test_allowed_tables_excludes_metadata():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE clientes (id INTEGER PRIMARY KEY, nome TEXT)")
        conn.execute("CREATE TABLE produtos (id INTEGER PRIMARY KEY, nome TEXT)")
        conn.execute("CREATE TABLE table_comments (id INTEGER, comment TEXT)")
        conn.execute("CREATE TABLE column_comments (id INTEGER, comment TEXT)")
        conn.commit()
        conn.close()

        tables = allowed_tables_from_db(db_path)
        assert "clientes" in tables
        assert "produtos" in tables
        assert "table_comments" not in tables
        assert "column_comments" not in tables
    finally:
        os.unlink(db_path)
