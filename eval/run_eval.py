"""Measure retrieval quality against eval/questions.json.

Metrics
  hit@1 / hit@3 : the expected article is the top result / among the top 3
  MRR           : mean reciprocal rank of the expected article
  abstain acc.  : out-of-scope questions correctly return no result

Usage: python -m eval.run_eval [--min-score 0.2] [--verbose]
Exits with status 1 if hit@3 drops below --fail-under, so it can run in CI.
"""
import argparse
import json
from pathlib import Path

from app.search import MIN_SCORE, search

QUESTIONS = Path(__file__).with_name("questions.json")


def evaluate(min_score: float, verbose: bool = False) -> dict:
    items = json.loads(QUESTIONS.read_text(encoding="utf-8"))
    answerable = [i for i in items if i["expected"]]
    out_of_scope = [i for i in items if not i["expected"]]
    hit1 = hit3 = 0
    rr = 0.0
    failures = []

    for item in answerable:
        results = search(item["q"], limit=10, min_score=min_score)
        slugs = []
        for r in results:  # keep first occurrence of each article
            if r["slug"] not in slugs:
                slugs.append(r["slug"])
        rank = slugs.index(item["expected"]) + 1 if item["expected"] in slugs else None
        hit1 += rank == 1
        hit3 += rank is not None and rank <= 3
        rr += 1 / rank if rank else 0
        if rank != 1:
            failures.append({"q": item["q"], "expected": item["expected"], "got": slugs[:3]})

    abstained = 0
    for item in out_of_scope:
        results = search(item["q"], limit=3, min_score=min_score)
        if not results:
            abstained += 1
        else:
            failures.append({"q": item["q"], "expected": None, "got": [r["slug"] for r in results]})

    n = len(answerable)
    report = {
        "min_score": min_score,
        "answerable": n,
        "out_of_scope": len(out_of_scope),
        "hit@1": round(hit1 / n, 3),
        "hit@3": round(hit3 / n, 3),
        "mrr": round(rr / n, 3),
        "abstain_accuracy": round(abstained / len(out_of_scope), 3) if out_of_scope else None,
    }
    if verbose:
        report["failures"] = failures
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-score", type=float, default=MIN_SCORE)
    parser.add_argument("--fail-under", type=float, default=0.8, help="minimum hit@3")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    result = evaluate(args.min_score, args.verbose)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["hit@3"] >= args.fail_under else 1)
