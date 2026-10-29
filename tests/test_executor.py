"""tests/test_executor.py - Unit tests for sql_executor.py.

Does NOT import query_service, retriever_node, sql_node, or api.
Uses a temporary SQLite database so no real DB or API key is needed.
The DATABASE_PATH env-var is patched to point at the temp file before each test.
"""
import os
import sqlite3
import tempfile

import pytest

import sql_executor


@pytest.fixture()
def tmp_db(monkeypatch):
    """Create a small temporary SQLite database and patch DATABASE_PATH."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE frutas (id INTEGER PRIMARY KEY, nome TEXT, preco REAL)"
    )
    conn.execute("INSERT INTO frutas VALUES (1, 'maca', 2.5)")
    conn.execute("INSERT INTO frutas VALUES (2, 'banana', 1.0)")
    conn.execute("INSERT INTO frutas VALUES (3, 'laranja', 3.0)")
    conn.commit()
    conn.close()

    monkeypatch.setenv("DATABASE_PATH", db_path)
    yield db_path
    os.unlink(db_path)


# ---------------------------------------------------------------------------
# execute_sql – basic correctness
# ---------------------------------------------------------------------------
def test_select_returns_correct_columns_and_rows(tmp_db):
    cols, rows = sql_executor.execute_sql("SELECT nome, preco FROM frutas ORDER BY id")
    assert cols == ["nome", "preco"]
    assert len(rows) == 3
    assert rows[0] == ("maca", 2.5)


# ---------------------------------------------------------------------------
# enforce_limit – adds LIMIT when missing
# ---------------------------------------------------------------------------
def test_limit_added_when_missing():
    sql = "SELECT * FROM frutas"
    result = sql_executor.enforce_limit(sql, limit=500)
    assert "LIMIT 500" in result


def test_limit_not_doubled_when_present():
    sql = "SELECT * FROM frutas LIMIT 10"
    result = sql_executor.enforce_limit(sql, limit=500)
    # Should NOT add another LIMIT
    assert result.count("LIMIT") == 1
    assert "LIMIT 10" in result


# ---------------------------------------------------------------------------
# open_ro_conn – connection is truly read-only
# ---------------------------------------------------------------------------
def test_write_through_ro_connection_fails(tmp_db):
    conn = sql_executor.open_ro_conn()
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("INSERT INTO frutas VALUES (99, 'uva', 5.0)")
    conn.close()
