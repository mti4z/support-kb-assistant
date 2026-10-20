"""Measure retrieval quality, with and without synonym expansion.

Three question sets, written in different words than the articles:
  dev      eval/questions.json          used to choose the score threshold
  heldout  eval/questions_heldout.json  written separately, but seen while writing the synonyms
  test     eval/questions_test.json     written before the synonyms and run only once at the end
  test_v2  eval/questions_test_v2.json  written before the 16 new articles, run once after freezing

Each set has answerable questions (with the expected article) and out-of-scope ones.

Metrics
  hit@1 / hit@3 : the expected article is the first result / among the first three
  MRR           : mean reciprocal rank of the expected article
  abstain       : share of out-of-scope questions correctly answered with "no match"

Usage
  python -m eval.run_eval                    # all sets, synonyms off and on
  python -m eval.run_eval --sets dev --verbose
Exits with status 1 if dev hit@3 (synonyms on) drops below --fail-under, for CI.
"""
import argparse
import json
from pathlib import Path

from app.search import MIN_SCORE, search

HERE = Path(__file__).parent
SETS = {
    "dev": HERE / "questions.json",
    "heldout": HERE / "questions_heldout.json",
    "test": HERE / "questions_test.json",
    "test_v2": HERE / "questions_test_v2.json",
}


def evaluate(items: list[dict], synonyms: bool, min_score: float) -> dict:
    answerable = [i for i in items if i["expected"]]
    out_of_scope = [i for i in items if not i["expected"]]
    hit1 = hit3 = abstained = 0
    rr = 0.0
    failures = []

    for item in answerable:
        results = search(item["q"], limit=10, min_score=min_score, synonyms=synonyms)
        slugs = []
        for r in results:  # keep the first occurrence of each article
            if r["slug"] not in slugs:
                slugs.append(r["slug"])
        rank = slugs.index(item["expected"]) + 1 if item["expected"] in slugs else None
        hit1 += rank == 1
        hit3 += rank is not None and rank <= 3
        rr += 1 / rank if rank else 0
        if rank != 1:
            failures.append(f"{item['q']!r} expected={item['expected']} got={slugs[:3]}")

    for item in out_of_scope:
        results = search(item["q"], limit=3, min_score=min_score, synonyms=synonyms)
        if not results:
            abstained += 1
        else:
            failures.append(f"{item['q']!r} expected=NO MATCH got={[r['slug'] for r in results][:3]}")

    n = len(answerable)
    return {
        "hit@1": hit1 / n,
        "hit@3": hit3 / n,
        "mrr": rr / n,
        "abstain": abstained / len(out_of_scope) if out_of_scope else float("nan"),
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sets", default="dev,heldout,test", help="comma separated")
    parser.add_argument("--min-score", type=float, default=MIN_SCORE)
    parser.add_argument("--fail-under", type=float, default=0.8, help="minimum dev hit@3")
    parser.add_argument("--verbose", action="store_true", help="list the failures")
    args = parser.parse_args()

    print(f"min_score={args.min_score}\n")
    print(f"{'set':8} {'synonyms':9} {'hit@1':>6} {'hit@3':>6} {'MRR':>6} {'abstain':>8}")
    status = 0
    for set_name in args.sets.split(","):
        items = json.loads(SETS[set_name].read_text(encoding="utf-8"))
        for synonyms in (False, True):
            r = evaluate(items, synonyms, args.min_score)
            label = "on" if synonyms else "off"
            print(f"{set_name:8} {label:9} {r['hit@1']:6.2f} {r['hit@3']:6.2f} {r['mrr']:6.2f} {r['abstain']:8.2f}")
            if args.verbose:
                for f in r["failures"]:
                    print(f"      miss: {f}")
            if set_name == "dev" and synonyms and r["hit@3"] < args.fail_under:
                status = 1
    return status


if __name__ == "__main__":
    raise SystemExit(main())
