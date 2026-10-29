#!/usr/bin/env python3
"""Helper utilities for chart selection and rendering in the Streamlit UI."""
from __future__ import annotations

import re
from typing import Optional

_DATE_PATTERNS = [
    re.compile(r"^\d{4}-\d{2}-\d{2}$"),
    re.compile(r"^\d{4}-\d{2}$"),
    re.compile(r"^\d{4}$"),
    re.compile(r"^[A-Za-z]{3} \d{1,2}, \d{4}$"),  # e.g. "Jul 15, 2023"
]


def _is_date_like(value: str) -> bool:
    s = value.strip()
    return any(p.match(s) for p in _DATE_PATTERNS)


def _first_numeric(values):
    """Return the first numeric-looking cell value as a float, or None."""
    for v in values:
        if v is None:
            continue
        try:
            cleaned = re.sub(r"[,\s\$]", "", str(v))
            if cleaned == "" or cleaned in ("inf", "-inf", "+inf"):
                continue
            f = float(cleaned)
            return f
        except ValueError:
            continue
    return None


def choose_chart(cols, rows) -> dict:
    """Pick the best Streamlit chart type for the current data.

    Returns ``{"type": "bar" | "line" | "scatter" | None, "x": col, "y": col}``.
    Rules (in priority order):
      1. Two numeric columns      -> scatter
      2. One date column + numeric -> line
      3. One categorical + numeric -> bar
      4. Otherwise                  -> None

    Categorical means no commas/dashes at all (ideal for charts).
    """
    nonempty = list(rows) if rows else []
    if not nonempty:
        return {"type": None}

    col_values = {}
    for col in cols:
        col_values[col] = [r.get(col) for r in nonempty]

    numeric_cols = []
    date_cols = []
    cat_cols = []

    for col, vals in col_values.items():
        sample = vals[0] if vals else None
        if sample is None:
            continue
        sample_str = str(sample)
        has_dash = "-" in sample_str
        has_comma = "," in sample_str
        looks_date = _is_date_like(sample_str)
        first_num = _first_numeric([v for v in vals if v is not None])
        if first_num is not None:
            numeric_cols.append(col)
        elif looks_date:
            date_cols.append(col)
        else:
            cat_cols.append(col)

    # 1. Scatter: two numeric columns available
    if len(numeric_cols) >= 2:
        x, y = numeric_cols[0], numeric_cols[1]
        return {"type": "scatter", "x": x, "y": y}

    # 2. Line: exactly one date column + one numeric column
    if len(date_cols) == 1 and len(numeric_cols) >= 1:
        x = date_cols[0]
        y = numeric_cols[0]
        return {"type": "line", "x": x, "y": y}

    # 3. Bar: exactly one categorical column + one numeric column
    if len(cat_cols) == 1 and len(numeric_cols) >= 1:
        x = cat_cols[0]
        y = numeric_cols[0]
        return {"type": "bar", "x": x, "y": y}

    # 4. Nothing appropriate
    return {"type": None}
