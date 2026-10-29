"""tests/test_chart_utils.py - Unit tests for chart_utils.py.

Does NOT import query_service, retriever_node, sql_node, or api.
Rows are plain lists-of-dicts as returned by the API.
"""
from chart_utils import choose_chart


# ---------------------------------------------------------------------------
# Rule 1 - date + number -> line
# ---------------------------------------------------------------------------
def test_year_month_plus_number_gives_line():
    cols = ["month", "revenue"]
    rows = [{"month": "2023-01", "revenue": 1000}, {"month": "2023-02", "revenue": 2000}]
    result = choose_chart(cols, rows)
    assert result["type"] == "line"
    assert result["x"] == "month"
    assert result["y"] == "revenue"


def test_full_date_plus_number_gives_line():
    cols = ["date", "sales"]
    rows = [{"date": "2023-08-15", "sales": 500}, {"date": "2023-08-16", "sales": 700}]
    result = choose_chart(cols, rows)
    assert result["type"] == "line"
    assert result["x"] == "date"
    assert result["y"] == "sales"


# ---------------------------------------------------------------------------
# Rule 2 - text + number, <=30 rows -> bar
# ---------------------------------------------------------------------------
def test_text_plus_number_gives_bar():
    cols = ["product", "qty"]
    rows = [
        {"product": "A", "qty": 10},
        {"product": "B", "qty": 20},
        {"product": "C", "qty": 5},
    ]
    result = choose_chart(cols, rows)
    assert result["type"] == "bar"
    assert result["x"] == "product"
    assert result["y"] == "qty"


# ---------------------------------------------------------------------------
# Rule 3 - single value -> None
# ---------------------------------------------------------------------------
def test_single_value_gives_none():
    cols = ["count"]
    rows = [{"count": 42}]
    result = choose_chart(cols, rows)
    assert result["type"] is None


# ---------------------------------------------------------------------------
# Rule 4 - two numeric columns, no date/text -> None (no scatter rule)
# ---------------------------------------------------------------------------
def test_two_numeric_gives_none():
    cols = ["x", "y"]
    rows = [{"x": 1, "y": 2}, {"x": 3, "y": 4}]
    result = choose_chart(cols, rows)
    assert result["type"] is None


# ---------------------------------------------------------------------------
# Rule 5 - more than 30 rows -> None even if text+number
# ---------------------------------------------------------------------------
def test_more_than_30_rows_gives_none():
    cols = ["name", "val"]
    rows = [{"name": f"item{i}", "val": i} for i in range(31)]
    result = choose_chart(cols, rows)
    assert result["type"] is None


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------
def test_empty_rows_give_none():
    result = choose_chart(["col"], [])
    assert result["type"] is None


def test_none_values_in_number_column_do_not_crash():
    cols = ["month", "revenue"]
    rows = [
        {"month": "2023-01", "revenue": None},
        {"month": "2023-02", "revenue": 500},
        {"month": "2023-03", "revenue": None},
    ]
    # Should not raise; type may be line or None depending on threshold
    result = choose_chart(cols, rows)
    assert "type" in result
