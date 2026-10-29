#!/usr/bin/env python3
"""eval/regrade.py - Re-execute and re-score a results JSON file.

Usage
-----
    python eval/regrade.py                           # newest file in eval/results/
    python eval/regrade.py eval/results/results-20261029T192658Z.json

For each question the script runs both generated_sql and gold_sql against
data/xpto_empresa.db (read-only), then applies two verdicts:

STRICT
    Same column names (alias-insensitive / order-insensitive) AND the same
    multiset of rows when both row-sets are sorted.

RELAXED
    Same number of rows AND every *column of values* in the generated result
    matches some column of values in the gold result (or vice-versa) by sorted
    list comparison.  Column names and column order are ignored.

No LLM is called.  Output is printed to stdout and saved to
eval/results/regrade-<timestamp>.json.
"""

import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
REPO_ROOT   = Path(__file__).resolve().parent.parent
DB_PATH     = REPO_ROOT / "data" / "xpto_empresa.db"
RESULTS_DIR = REPO_ROOT / "eval" / "results"


def _newest_results() -> Path:
    """Return the most-recently-modified JSON in eval/results/."""
    files = sorted(RESULTS_DIR.glob("results-*.json"), key=lambda p: p.stat().st_mtime)
    if not files:
        raise FileNotFoundError(f"No results-*.json found in {RESULTS_DIR}")
    return files[-1]


# ---------------------------------------------------------------------------
# SQL execution helpers
# ---------------------------------------------------------------------------
def _run_sql(conn: sqlite3.Connection, sql: str):
    """Execute *sql* and return (columns, rows).  Returns (None, error_str) on error."""
    try:
        cur = conn.execute(sql)
        cols = [d[0] for d in cur.description] if cur.description else []
        rows = cur.fetchall()
        return cols, rows
    except Exception as exc:
        return None, str(exc)


def _sort_rows(rows):
    """Sort rows deterministically (convert each value to str first)."""
    return sorted([tuple(str(v) for v in r) for r in rows])


# ---------------------------------------------------------------------------
# Verdict helpers
# ---------------------------------------------------------------------------
def _normalize_col(name: str) -> str:
    """Lower-case and strip underscores/spaces so alias differences don't matter."""
    return re.sub(r"[_\s]+", "", name.lower())


def strict_verdict(gen_cols, gen_rows, gold_cols, gold_rows) -> bool:
    """Columns match (order-insensitive, alias-insensitive) AND sorted rows match."""
    if gen_cols is None or gold_cols is None:
        return False
    if len(gen_cols) != len(gold_cols):
        return False
    if sorted(_normalize_col(c) for c in gen_cols) != sorted(_normalize_col(c) for c in gold_cols):
        return False
    return _sort_rows(gen_rows) == _sort_rows(gold_rows)


def relaxed_verdict(gen_cols, gen_rows, gold_cols, gold_rows) -> bool:
    """Row count matches AND every column of values in one result matches
    some column of values in the other (by sorted list, ignoring name/order)."""
    if gen_cols is None or gold_cols is None:
        return False
    if len(gen_rows) != len(gold_rows):
        return False
    if len(gen_rows) == 0:          # both empty -> trivially equivalent
        return True

    def col_value_sets(rows, n_cols):
        """Return a list of sorted-value-lists, one per column."""
        return [sorted(str(rows[r][c]) for r in range(len(rows))) for c in range(n_cols)]

    gen_cols_vals  = col_value_sets(gen_rows,  len(gen_cols))
    gold_cols_vals = col_value_sets(gold_rows, len(gold_cols))

    def all_covered(source, target):
        remaining = list(target)
        for col in source:
            if col in remaining:
                remaining.remove(col)
            else:
                return False
        return True

    return all_covered(gen_cols_vals, gold_cols_vals) or all_covered(gold_cols_vals, gen_cols_vals)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    # 1. Resolve input file
    if len(sys.argv) > 1:
        results_path = Path(sys.argv[1])
        if not results_path.is_absolute():
            results_path = REPO_ROOT / results_path
    else:
        results_path = _newest_results()

    print(f"Input : {results_path}")
    print(f"DB    : {DB_PATH}\n")

    with open(results_path, encoding="utf-8") as fh:
        data = json.load(fh)

    questions = data.get("results", data)   # handle both wrapped and bare list

    # 2. Open DB read-only
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)

    strict_correct  = 0
    relaxed_correct = 0
    relaxed_wrong   = []
    details         = []

    for q in questions:
        qid      = q.get("id", "?")
        text     = q.get("question", "")
        gen_sql  = q.get("generated_sql", "")
        gold_sql = q.get("gold_sql", "")

        gen_cols,  gen_rows  = _run_sql(conn, gen_sql)
        gold_cols, gold_rows = _run_sql(conn, gold_sql)

        sv = strict_verdict(gen_cols,  gen_rows,  gold_cols, gold_rows)
        rv = relaxed_verdict(gen_cols, gen_rows, gold_cols, gold_rows)

        if sv:
            strict_correct  += 1
        if rv:
            relaxed_correct += 1
        else:
            relaxed_wrong.append({
                "id":            qid,
                "question":      text,
                "generated_sql": gen_sql,
                "gold_sql":      gold_sql,
                "gen_error":     gen_rows  if gen_cols  is None else None,
                "gold_error":    gold_rows if gold_cols is None else None,
            })

        details.append({
            "id":            qid,
            "question":      text,
            "generated_sql": gen_sql,
            "gold_sql":      gold_sql,
            "strict":        sv,
            "relaxed":       rv,
        })

    conn.close()

    total       = len(questions)
    strict_acc  = strict_correct  / total if total else 0.0
    relaxed_acc = relaxed_correct / total if total else 0.0

    # 3. Print summary
    width = 62
    print("=" * width)
    print(f"  Questions  : {total}")
    print(f"  STRICT     : {strict_correct}/{total}  ({strict_acc:.1%})")
    print(f"  RELAXED    : {relaxed_correct}/{total}  ({relaxed_acc:.1%})")
    print("=" * width)

    if relaxed_wrong:
        print(f"\nFailing under RELAXED ({len(relaxed_wrong)} questions):\n")
        for item in relaxed_wrong:
            err_gen  = f"  [ERROR: {item['gen_error']}]"  if item["gen_error"]  else ""
            err_gold = f"  [ERROR: {item['gold_error']}]" if item["gold_error"] else ""
            print(f"  #{item['id']:>2}  {item['question']}")
            print(f"        GEN : {item['generated_sql']}{err_gen}")
            print(f"        GOLD: {item['gold_sql']}{err_gold}")
            print()
    else:
        print("\nAll questions pass under RELAXED scoring.")

    # 4. Save output
    ts       = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = RESULTS_DIR / f"regrade-{ts}.json"
    output   = {
        "timestamp":        ts,
        "input_file":       str(results_path.name),
        "total":            total,
        "strict_correct":   strict_correct,
        "strict_accuracy":  round(strict_acc,  4),
        "relaxed_correct":  relaxed_correct,
        "relaxed_accuracy": round(relaxed_acc, 4),
        "relaxed_wrong":    relaxed_wrong,
        "details":          details,
    }
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(output, fh, indent=2, ensure_ascii=False)

    print(f"Saved : {out_path}")


if __name__ == "__main__":
    main()
