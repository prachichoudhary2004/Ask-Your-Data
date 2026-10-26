import sqlite3
import json

conn = sqlite3.connect("data/xpto_empresa.db")
cur = conn.cursor()

with open("eval/questions.json", "r", encoding="utf-8") as f:
    questions = json.load(f)

print(f"Total questions: {len(questions)}")
pt_count = sum(1 for q in questions if q["language"] == "pt")
tricky_count = sum(1 for q in questions if q["tricky"])
print(f"Portuguese: {pt_count}")
print(f"Tricky: {tricky_count}")

categories = {}
for q in questions:
    categories.setdefault(q["category"], 0)
    categories[q["category"]] += 1
print(f"Categories: {categories}")

print("\n--- Testing all gold SQLs ---")
failed = 0
for q in questions:
    try:
        cur.execute(q["gold_sql"])
        cols = [c[0] for c in cur.description] if cur.description else []
        rows = cur.fetchall()
        print(f"OK   #{q['id']:2d} [{q['category']:<14s}] {len(rows):3d} rows | {q['question'][:60]}")
    except Exception as e:
        print(f"FAIL #{q['id']:2d} [{q['category']:<14s}] {e}")
        failed += 1

print(f"\n{failed} failures out of {len(questions)}")
conn.close()
