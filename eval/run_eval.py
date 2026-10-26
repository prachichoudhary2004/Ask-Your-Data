#!/usr/bin/env python3
"""Evaluation harness for the Text-to-SQL RAG chatbot.

Each question is classified as one of four outcomes:
  correct          - generated result set matches gold SQL result set
  wrong            - pipeline succeeded but result does not match gold
  validator_rejected - query_service raised ValueError (SQL validation failed)
  infra_error      - transient failure (rate-limit 429, timeout, connection error)

Key features:
  - Exponential backoff with Retry-After header support for infra_errors
  - Configurable --sleep between questions (to pace token usage)
  - Dual accuracy: over all questions AND over completed questions only
  - --resume <results_file>  re-runs only infra_error questions, merges results
  - --limit N  run only the first N questions
  - --category CAT  run only questions in that category
  - LLM response cache keyed by (question_text, prompt_hash) in eval/cache/

Usage:
    uv run python eval/run_eval.py
    uv run python eval/run_eval.py --sleep 2
    uv run python eval/run_eval.py --limit 10 --category filter
    uv run python eval/run_eval.py --resume eval/results/results-<ts>.json

Exit codes:
    0  - run completed, no infra_errors remain
    1  - at least one infra_error remains (partial run)
"""
import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from typing import Optional

# ---------------------------------------------------------------------------
# Path setup: make project root importable when run from any directory
# ---------------------------------------------------------------------------
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(ROOT, ".env"))

from query_service import query, QueryRequest  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
QUESTIONS_FILE = os.path.join(os.path.dirname(__file__), "questions.json")
RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")
CACHE_DIR = os.path.join(os.path.dirname(__file__), "cache")

# Outcome labels (canonical strings stored in results JSON)
CORRECT = "correct"
WRONG = "wrong"
VALIDATOR_REJECTED = "validator_rejected"
INFRA_ERROR = "infra_error"

# Sentinel for None in comparison
_NULL = "__NULL__"

# Patterns that identify transient / infrastructure errors
_INFRA_PATTERNS = re.compile(
    r"(rate.?limit|429|too many requests|timeout|timed.?out|"
    r"connection.?error|connection.?reset|service.?unavailable|"
    r"503|504|read.?timeout)",
    re.IGNORECASE,
)
# Retry-After header sometimes embedded in the error message
_RETRY_AFTER_RE = re.compile(r"try again in\s+([\d.]+)\s*s", re.IGNORECASE)

# Default retry settings
DEFAULT_BACKOFF_BASE = 4.0   # seconds for first retry
DEFAULT_MAX_RETRIES = 5
DEFAULT_BACKOFF_MAX = 300.0  # cap at 5 minutes per attempt


# ===========================================================================
# Result-set comparison helpers
# ===========================================================================

def _normalise_value(v) -> str:
    """Canonical string form of a single cell value.

    - None  -> sentinel
    - float -> rounded to 4 d.p. to absorb floating-point noise
    - other -> str, stripped, lower-cased
    """
    if v is None:
        return _NULL
    if isinstance(v, float):
        return str(round(v, 4))
    return str(v).strip().lower()


def _row_signature(row_dict: dict) -> tuple:
    """Sorted tuple of normalised cell values -- column-name agnostic."""
    return tuple(sorted(_normalise_value(v) for v in row_dict.values()))


def rows_match(result_rows: list[dict], gold_rows: list[dict]) -> bool:
    """Order-insensitive, alias-tolerant result-set comparison.

    Compares the multiset of rows where each row is its sorted-value tuple,
    so column names and ordering are both irrelevant.
    """
    if len(result_rows) != len(gold_rows):
        return False
    if not result_rows:
        return True
    return (
        Counter(map(_row_signature, result_rows))
        == Counter(map(_row_signature, gold_rows))
    )


# ===========================================================================
# Outcome classification helpers
# ===========================================================================

def _is_infra_error(exc: Exception) -> bool:
    """Return True if the exception looks like a transient / infra problem."""
    return bool(_INFRA_PATTERNS.search(str(exc)))


def _is_validator_rejection(exc: Exception) -> bool:
    """Return True if query_service raised a validation ValueError."""
    return isinstance(exc, ValueError)


def _extract_retry_after(exc: Exception) -> Optional[float]:
    """Parse a suggested wait time (seconds) from the exception message."""
    m = _RETRY_AFTER_RE.search(str(exc))
    return float(m.group(1)) if m else None


# ===========================================================================
# Gold-SQL execution (direct sqlite3, bypasses the pipeline)
# ===========================================================================

def execute_gold_sql(sql: str) -> tuple[list[str], list[dict]]:
    """Execute *sql* read-only, return (column_names, rows_as_dicts)."""
    db_path = os.getenv("DATABASE_PATH")
    conn = sqlite3.connect(
        f"file:{db_path}?mode=ro", uri=True, check_same_thread=False
    )
    try:
        cur = conn.cursor()
        cur.execute(sql)
        cols = [c[0] for c in cur.description] if cur.description else []
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        return cols, rows
    finally:
        conn.close()


# ===========================================================================
# LLM response cache
# ===========================================================================

def _cache_key(question: str) -> str:
    """Stable cache filename: sha256 of the question text."""
    digest = hashlib.sha256(question.encode("utf-8")).hexdigest()[:16]
    return digest


def _cache_load(question: str) -> Optional[dict]:
    """Return cached pipeline result dict, or None on miss."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, f"{_cache_key(question)}.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            entry = json.load(f)
        # Validate structure
        if "question" in entry and entry["question"] == question:
            return entry["result"]
    except Exception:
        pass
    return None


def _cache_store(question: str, result: dict) -> None:
    """Persist a pipeline result to the cache."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, f"{_cache_key(question)}.json")
    entry = {"question": question, "result": result, "cached_at": _now_iso()}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(entry, f, indent=2, ensure_ascii=False, default=str)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


# ===========================================================================
# Single-question evaluation (with retries)
# ===========================================================================

def _run_one_question(
    question_text: str,
    max_retries: int = DEFAULT_MAX_RETRIES,
    backoff_base: float = DEFAULT_BACKOFF_BASE,
    use_cache: bool = True,
) -> tuple[str, Optional[str], list[dict], list[str], float, Optional[str]]:
    """Run the pipeline for one question, retrying infra errors.

    Returns
    -------
    outcome       : CORRECT | WRONG | VALIDATOR_REJECTED | INFRA_ERROR
    generated_sql : str or None
    gen_rows      : list[dict]
    gen_cols      : list[str]
    elapsed       : wall-clock seconds (sum across all attempts)
    error_msg     : last error string if not CORRECT/WRONG, else None
    """
    # --- cache hit? ---
    if use_cache:
        cached = _cache_load(question_text)
        if cached is not None:
            sql = cached.get("sql")
            cols = list(cached.get("cols") or [])
            rows = cached.get("rows") or []
            elapsed = cached.get("latency_seconds", 0.0)
            # Return as 'wrong' tentatively; caller will compare vs gold
            return WRONG, sql, rows, cols, elapsed, None

    total_elapsed = 0.0
    last_exc_msg: Optional[str] = None

    for attempt in range(max_retries + 1):
        if attempt > 0:
            # Calculate backoff, respecting Retry-After from last error
            suggested = _extract_retry_after(Exception(last_exc_msg or ""))
            wait = min(
                suggested if suggested else backoff_base * (2 ** (attempt - 1)),
                DEFAULT_BACKOFF_MAX,
            )
            print(f"      [retry {attempt}/{max_retries}] backing off {wait:.1f}s ...")
            time.sleep(wait)

        start = time.perf_counter()
        try:
            svc_result = query(QueryRequest(question=question_text))
            elapsed = time.perf_counter() - start
            total_elapsed += elapsed

            sql = svc_result.get("sql")
            cols = list(svc_result.get("cols") or [])
            rows = svc_result.get("rows") or []

            # Store successful result in cache
            if use_cache:
                _cache_store(
                    question_text,
                    {"sql": sql, "cols": cols, "rows": rows,
                     "latency_seconds": round(total_elapsed, 4)},
                )

            # Return tentative WRONG; caller compares vs gold
            return WRONG, sql, rows, cols, total_elapsed, None

        except Exception as exc:
            elapsed = time.perf_counter() - start
            total_elapsed += elapsed
            last_exc_msg = f"{type(exc).__name__}: {exc}"

            if _is_validator_rejection(exc):
                return VALIDATOR_REJECTED, None, [], [], total_elapsed, last_exc_msg

            if _is_infra_error(exc):
                if attempt < max_retries:
                    continue  # will sleep + retry
                # Exhausted retries
                return INFRA_ERROR, None, [], [], total_elapsed, last_exc_msg

            # Unknown exception -- treat as wrong (not transient)
            return WRONG, None, [], [], total_elapsed, last_exc_msg

    # Should not reach here
    return INFRA_ERROR, None, [], [], total_elapsed, last_exc_msg


# ===========================================================================
# Core evaluation loop
# ===========================================================================

def run_eval(
    questions_path: str,
    sleep_between: float = 0.0,
    limit: Optional[int] = None,
    category_filter: Optional[str] = None,
    max_retries: int = DEFAULT_MAX_RETRIES,
    use_cache: bool = True,
    resume_data: Optional[dict] = None,
) -> dict:
    """Run the evaluation suite, return a results dict.

    Parameters
    ----------
    questions_path  : path to questions.json
    sleep_between   : seconds to sleep between questions (rate-pacing)
    limit           : if set, stop after this many questions
    category_filter : if set, only evaluate questions in this category
    max_retries     : max retry attempts for infra_errors
    use_cache       : whether to use/write the LLM response cache
    resume_data     : if resuming, the previously loaded results dict
    """
    with open(questions_path, "r", encoding="utf-8") as f:
        all_questions = json.load(f)

    # Apply category filter
    questions = [
        q for q in all_questions
        if category_filter is None or q.get("category") == category_filter
    ]

    # If resuming, only re-run infra_error items
    resume_infra_ids: set[int] = set()
    prior_results_by_id: dict[int, dict] = {}
    if resume_data:
        for r in resume_data.get("results", []):
            prior_results_by_id[r["id"]] = r
            if r.get("outcome") == INFRA_ERROR:
                resume_infra_ids.add(r["id"])
        questions = [q for q in questions if q.get("id") in resume_infra_ids]
        print(f"  [resume] Re-running {len(questions)} infra_error question(s).")

    # Apply limit
    if limit is not None:
        questions = questions[:limit]

    total = len(questions)
    print(f"\n{'='*70}")
    flags = []
    if category_filter:
        flags.append(f"category={category_filter}")
    if limit:
        flags.append(f"limit={limit}")
    if resume_data:
        flags.append("RESUME")
    flags_str = f"  [{', '.join(flags)}]" if flags else ""
    print(f"  Text-to-SQL Eval Harness  --  {total} questions{flags_str}")
    print(f"{'='*70}\n")

    # Per-question result records (new this run)
    new_records: list[dict] = []
    # Per-category stats
    cat_stats: dict[str, dict] = {}

    for idx, q in enumerate(questions, 1):
        qid = q.get("id", idx)
        question_text = q["question"]
        gold_sql = q["gold_sql"]
        category = q.get("category", "unknown")
        language = q.get("language", "en")
        is_tricky = q.get("tricky", False)

        if category not in cat_stats:
            cat_stats[category] = {
                "total": 0, "correct": 0, "wrong": 0,
                "validator_rejected": 0, "infra_error": 0, "latencies": [],
            }
        cat_stats[category]["total"] += 1

        # --- Inter-question sleep (for rate pacing) ---
        if idx > 1 and sleep_between > 0:
            time.sleep(sleep_between)

        # --- Run pipeline (with retries for infra errors) ---
        outcome, generated_sql, gen_rows, gen_cols, elapsed, error_msg = \
            _run_one_question(question_text, max_retries=max_retries,
                              use_cache=use_cache)

        cat_stats[category]["latencies"].append(elapsed)

        # --- Execute gold SQL ---
        gold_cols: list[str] = []
        gold_rows: list[dict] = []
        gold_error: Optional[str] = None
        try:
            gold_cols, gold_rows = execute_gold_sql(gold_sql)
        except Exception as exc:
            gold_error = f"GOLD-SQL-ERROR: {type(exc).__name__}: {exc}"
            # Treat as infra-level failure (our test infrastructure broke)
            outcome = INFRA_ERROR
            error_msg = gold_error

        # --- Finalise outcome ---
        if outcome not in (VALIDATOR_REJECTED, INFRA_ERROR):
            if rows_match(gen_rows, gold_rows):
                outcome = CORRECT
            else:
                outcome = WRONG

        cat_stats[category][outcome] += 1

        # --- Per-line progress ---
        mark = {
            CORRECT: "OK  ",
            WRONG: "MISS",
            VALIDATOR_REJECTED: "REJT",
            INFRA_ERROR: "ERR!",
        }[outcome]

        tricky_tag = " [tricky]" if is_tricky else ""
        cached_tag = " [cached]" if (use_cache and _cache_load(question_text) is not None
                                      and outcome != INFRA_ERROR
                                      and generated_sql is not None) else ""
        print(
            f"  {mark}  #{qid:<3d} [{category:<14s}] [{language}]{tricky_tag}{cached_tag}"
            f"  ({elapsed:.2f}s)  {question_text[:50]}"
        )

        # --- Build record ---
        record: dict = {
            "id": qid,
            "question": question_text,
            "category": category,
            "language": language,
            "tricky": is_tricky,
            "outcome": outcome,
            "generated_sql": generated_sql,
            "gold_sql": gold_sql,
            "generated_row_count": len(gen_rows),
            "gold_row_count": len(gold_rows),
            "latency_seconds": round(elapsed, 4),
        }
        if error_msg:
            record["error"] = error_msg
        new_records.append(record)

    # ===========================================================================
    # Merge with prior results if resuming
    # ===========================================================================
    if resume_data:
        merged_by_id = dict(prior_results_by_id)
        for r in new_records:
            merged_by_id[r["id"]] = r  # new run overwrites old
        all_records = sorted(merged_by_id.values(), key=lambda x: x["id"])
    else:
        all_records = new_records

    # ===========================================================================
    # Compute summary metrics over all_records
    # ===========================================================================
    n_total = len(all_records)
    n_correct = sum(1 for r in all_records if r["outcome"] == CORRECT)
    n_wrong = sum(1 for r in all_records if r["outcome"] == WRONG)
    n_validator = sum(1 for r in all_records if r["outcome"] == VALIDATOR_REJECTED)
    n_infra = sum(1 for r in all_records if r["outcome"] == INFRA_ERROR)
    n_completed = n_total - n_infra  # excludes infra errors

    acc_all = n_correct / n_total if n_total else 0.0
    acc_completed = n_correct / n_completed if n_completed else 0.0

    # For the per-category summary we rebuild from all_records
    full_cat: dict[str, dict] = {}
    all_latencies = []
    for r in all_records:
        c = r["category"]
        if c not in full_cat:
            full_cat[c] = {
                "total": 0, CORRECT: 0, WRONG: 0,
                VALIDATOR_REJECTED: 0, INFRA_ERROR: 0, "latencies": [],
            }
        full_cat[c]["total"] += 1
        full_cat[c][r["outcome"]] += 1
        full_cat[c]["latencies"].append(r["latency_seconds"])
        all_latencies.append(r["latency_seconds"])

    avg_latency = sum(all_latencies) / len(all_latencies) if all_latencies else 0.0

    # ===========================================================================
    # Print summary
    # ===========================================================================
    print(f"\n{'='*70}")
    print("  SUMMARY")
    print(f"{'='*70}")
    hdr = (f"  {'Category':<16s} {'Tot':>4s} {'OK':>5s} {'MISS':>5s} "
           f"{'REJT':>5s} {'ERR':>5s} {'Acc(all)':>9s} {'AvgLat':>8s}")
    print(hdr)
    print(f"  {'-'*62}")
    for cat in sorted(full_cat):
        s = full_cat[cat]
        cat_n = s["total"]
        cat_ok = s[CORRECT]
        cat_inf = s[INFRA_ERROR]
        cat_comp = cat_n - cat_inf
        cat_acc = cat_ok / cat_n if cat_n else 0.0
        cat_lat = (sum(s["latencies"]) / len(s["latencies"])
                   if s["latencies"] else 0.0)
        print(
            f"  {cat:<16s} {cat_n:>4d} {cat_ok:>5d} {s[WRONG]:>5d} "
            f"{s[VALIDATOR_REJECTED]:>5d} {cat_inf:>5d} "
            f"{cat_acc:>8.1%} {cat_lat:>7.2f}s"
        )
    print(f"  {'-'*62}")
    print(
        f"  {'OVERALL':<16s} {n_total:>4d} {n_correct:>5d} {n_wrong:>5d} "
        f"{n_validator:>5d} {n_infra:>5d} {acc_all:>8.1%} {avg_latency:>7.2f}s"
    )
    print()
    print(f"  Accuracy (all {n_total} questions)          : {acc_all:.1%}")
    print(f"  Accuracy ({n_completed} completed, excl. infra) : {acc_completed:.1%}")
    if n_infra:
        print(f"\n  *** INFRA ERRORS: {n_infra} question(s) could not be evaluated "
              f"(rate-limit / timeout). ***")
        print(f"  *** Re-run with --resume to retry them.                        ***")
    print()

    # ===========================================================================
    # Print failures detail
    # ===========================================================================
    non_correct = [r for r in all_records if r["outcome"] != CORRECT]
    if non_correct:
        print(f"  NON-CORRECT RESULTS  ({len(non_correct)}/{n_total})")
        print(f"  {'-'*62}")
        for r in non_correct:
            outcome_label = r["outcome"].upper()
            print(f"  [{outcome_label}] #{r['id']}  {r['question']}")
            if r.get("error"):
                err_short = str(r["error"])[:120]
                print(f"       Error      : {err_short}")
            if r["outcome"] in (WRONG, VALIDATOR_REJECTED):
                print(f"       Generated  : {r.get('generated_sql', 'N/A')}")
                print(f"       Gold       : {r['gold_sql']}")
                print(f"       Rows gen/gold: {r['generated_row_count']}/{r['gold_row_count']}")
            print()
    else:
        print("  All queries correct!\n")

    # ===========================================================================
    # Build results payload and save
    # ===========================================================================
    timestamp = _now_iso()
    results_payload = {
        "timestamp": timestamp,
        "summary": {
            "total": n_total,
            "correct": n_correct,
            "wrong": n_wrong,
            "validator_rejected": n_validator,
            "infra_error": n_infra,
            "completed": n_completed,
            "accuracy_all": round(acc_all, 4),
            "accuracy_completed": round(acc_completed, 4),
            "average_latency_seconds": round(avg_latency, 4),
            "categories": {
                cat: {
                    "total": s["total"],
                    CORRECT: s[CORRECT],
                    WRONG: s[WRONG],
                    VALIDATOR_REJECTED: s[VALIDATOR_REJECTED],
                    INFRA_ERROR: s[INFRA_ERROR],
                    "accuracy_all": round(
                        s[CORRECT] / s["total"] if s["total"] else 0.0, 4
                    ),
                    "accuracy_completed": round(
                        s[CORRECT] / (s["total"] - s[INFRA_ERROR])
                        if (s["total"] - s[INFRA_ERROR]) > 0 else 0.0,
                        4,
                    ),
                    "average_latency_seconds": round(
                        sum(s["latencies"]) / len(s["latencies"])
                        if s["latencies"] else 0.0,
                        4,
                    ),
                }
                for cat, s in sorted(full_cat.items())
            },
        },
        "results": all_records,
    }

    os.makedirs(RESULTS_DIR, exist_ok=True)
    # When resuming, overwrite the original file so it stays the canonical record
    if resume_data and resume_data.get("_source_path"):
        out_path = resume_data["_source_path"]
    else:
        out_path = os.path.join(RESULTS_DIR, f"results-{timestamp}.json")

    with open(out_path, "w", encoding="utf-8") as fout:
        json.dump(results_payload, fout, indent=2, ensure_ascii=False, default=str)
    print(f"  Results saved -> {out_path}\n")

    return results_payload


# ===========================================================================
# CLI
# ===========================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Run text-to-SQL evaluation harness",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--questions",
        default=QUESTIONS_FILE,
        metavar="FILE",
        help="Questions JSON file (default: eval/questions.json)",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=1.0,
        metavar="SECS",
        help="Seconds to sleep between questions (default: 1.0; set 0 to disable)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Evaluate only the first N questions (after filtering)",
    )
    parser.add_argument(
        "--category",
        default=None,
        metavar="CAT",
        help="Evaluate only questions in this category (e.g. filter, join, count)",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=DEFAULT_MAX_RETRIES,
        metavar="N",
        help=f"Max retry attempts for infra_errors (default: {DEFAULT_MAX_RETRIES})",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Disable the LLM response cache (always call the pipeline)",
    )
    parser.add_argument(
        "--resume",
        default=None,
        metavar="RESULTS_FILE",
        help="Re-run only infra_error questions from a previous results file",
    )
    args = parser.parse_args()

    # ---- Load resume file if given ----
    resume_data: Optional[dict] = None
    if args.resume:
        if not os.path.exists(args.resume):
            print(f"ERROR: resume file not found: {args.resume}", file=sys.stderr)
            sys.exit(2)
        with open(args.resume, "r", encoding="utf-8") as f:
            resume_data = json.load(f)
        resume_data["_source_path"] = os.path.abspath(args.resume)
        n_infra_prev = resume_data.get("summary", {}).get("infra_error", "?")
        print(f"Resuming from: {args.resume}  ({n_infra_prev} infra_error(s) to retry)")

    results = run_eval(
        questions_path=args.questions,
        sleep_between=args.sleep,
        limit=args.limit,
        category_filter=args.category,
        max_retries=args.max_retries,
        use_cache=not args.no_cache,
        resume_data=resume_data,
    )

    summary = results["summary"]
    print(f"  Baseline accuracy (all)       : {summary['accuracy_all']:.1%}")
    print(f"  Baseline accuracy (completed) : {summary['accuracy_completed']:.1%}\n")

    # Exit non-zero if any infra_errors remain -- signals a partial run
    if summary["infra_error"] > 0:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
