#!/usr/bin/env python3
"""chart_utils.py - Chart selection helper for the Streamlit UI.

Public API
----------
choose_chart(cols, rows) -> dict
    cols : list[str]   - column names
    rows : list[dict]  - rows as returned by the API (list of {col: value})

Returns
-------
{"type": "bar" | "line" | None, "x": col_name, "y": col_name}

Rules (in priority order)
--------------------------
1. First column contains date/month strings (YYYY-MM-DD or YYYY-MM) AND there
   is at least one numeric column -> "line"
2. Exactly one text column AND exactly one numeric column AND at most 30 rows
   -> "bar"
3. Any other case -> None
"""
from __future__ import annotations

import re

_DATE_RE = re.compile(r"^\d{4}-\d{2}(-\d{2})?$")  # YYYY-MM or YYYY-MM-DD


def _is_date_like(value) -> bool:
    """Return True when *value* looks like a date or year-month string."""
    return bool(_DATE_RE.match(str(value).strip()))


def _to_float(value) -> float | None:
    """Convert *value* to float, returning None on failure or if None."""
    if value is None:
        return None
    try:
        cleaned = re.sub(r"[,\s$]", "", str(value))
        if not cleaned or cleaned in ("inf", "-inf", "+inf", "nan"):
            return None
        return float(cleaned)
    except ValueError:
        return None


def _col_is_numeric(col: str, rows: list[dict]) -> bool:
    """True when the majority of non-None values in *col* parse as floats."""
    vals = [_to_float(r.get(col)) for r in rows]
    good = [v for v in vals if v is not None]
    return len(good) > 0 and len(good) >= len(rows) * 0.5


def choose_chart(cols: list, rows: list) -> dict:
    """Pick the best chart type for the given data.

    Parameters
    ----------
    cols : list[str]
    rows : list[dict]  (each dict maps column name -> value)

    Returns
    -------
    {"type": "bar" | "line" | None, "x": str, "y": str}
    """
    if not rows or not cols:
        return {"type": None}

    n_rows = len(rows)

    # Classify each column
    date_cols    = []
    numeric_cols = []
    text_cols    = []

    for col in cols:
        if _col_is_numeric(col, rows):
            numeric_cols.append(col)
        elif rows and _is_date_like(rows[0].get(col, "")):
            date_cols.append(col)
        else:
            text_cols.append(col)

    # Rule 1 – line: first column is date-like + at least one numeric col
    if date_cols and numeric_cols and cols[0] in date_cols:
        return {"type": "line", "x": date_cols[0], "y": numeric_cols[0]}

    # Rule 2 – bar: one text col + one numeric col + <=30 rows
    if len(text_cols) == 1 and len(numeric_cols) == 1 and n_rows <= 30:
        return {"type": "bar", "x": text_cols[0], "y": numeric_cols[0]}

    return {"type": None}
