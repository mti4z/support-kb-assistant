"""Print the cases of answers.json that deserve a human look.

    python -m eval.review_answers answers.json
"""
import json
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "answers.json"
cases = json.load(open(path, encoding="utf-8"))["cases"]

groups = {
    "NOT GROUNDED (the judge found claims the passages do not support)":
        [c for c in cases if not c["grounded"]],
    "DECLINED although the right article reached Claude":
        [c for c in cases if c["expected"] and c["in_context"] and c["refused"]],
    "ANSWERED although the expected article was not among the 3 best passages (check they are right)":
        [c for c in cases if c["expected"] and not c["in_context"] and not c["refused"]],
    "ANSWERED an out-of-scope question (should be none)":
        [c for c in cases if not c["expected"] and not c["refused"]],
}
for title, items in groups.items():
    print(f"\n=== {title}: {len(items)} ===")
    for c in items:
        print(f"\nQ: {c['q']}\nJudge: {c['reason']}\nAnswer: {c['answer'][:700]}")
