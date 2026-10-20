"""Evaluate the answers WRITTEN BY CLAUDE (not the retrieval).

For each question we retrieve passages (synonyms on), ask Claude for an answer
using only those passages, and then ask Claude again, as a judge, to grade it:

  answerable questions (also split by whether the expected article reached Claude)
    grounded  : every claim in the answer is supported by the passages
    relevant  : the answer actually addresses the question
  out-of-scope questions
    refused   : the answer says it does not know / suggests a ticket
                (counted as correct also when retrieval found nothing)

Needs ANTHROPIC_API_KEY. A full run on test + test_v2 is about 100 short calls
(answer + judge), roughly 1 USD or less. Use --dry-run first and --limit to try.

    python -m eval.run_answer_eval --dry-run
    python -m eval.run_answer_eval --sets test_v2 --verbose
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

from app.answer import MODEL, NO_ANSWER, _with_claude, build_context, message_text
from app.search import search
from eval.run_eval import SETS

JUDGE_MODEL = os.environ.get("ANSWER_JUDGE_MODEL", MODEL)

JUDGE_PROMPT = """You grade answers from an IT support assistant that must use ONLY the passages it was given.

Passages:
{passages}

Question: {question}

Assistant answer:
{answer}

Reply with ONLY a JSON object, no other text:
{{"grounded": true/false, "relevant": true/false, "refused": true/false, "reason": "one short sentence"}}

- grounded: every factual claim or step in the answer is supported by the passages (generic politeness does not count as a claim).
- relevant: the answer addresses what the question asks.
- refused: the answer says it does not know, or that the passages do not cover it, and suggests another route such as a ticket."""


def _judge_call(prompt: str) -> str:
    import anthropic

    msg = anthropic.Anthropic().messages.create(
        model=JUDGE_MODEL, max_tokens=1000, messages=[{"role": "user", "content": prompt}]
    )
    return message_text(msg)


def parse_verdict(text: str) -> dict:
    """Pull the JSON object out of the judge's reply. Raises ValueError if absent."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError(f"no JSON in judge reply: {text[:80]!r}")
    data = json.loads(match.group(0))
    return {
        "grounded": bool(data.get("grounded")),
        "relevant": bool(data.get("relevant")),
        "refused": bool(data.get("refused")),
        "reason": str(data.get("reason", "")),
    }


def grade_item(item: dict, answer_fn=_with_claude, judge_fn=_judge_call, min_score=None) -> dict:
    kwargs = {} if min_score is None else {"min_score": min_score}
    passages = search(item["q"], limit=5, synonyms=True, **kwargs)
    row = {"q": item["q"], "expected": item["expected"], "retrieved": bool(passages),
           "in_context": bool(item["expected"]) and item["expected"] in {p["slug"] for p in passages[:3]}}
    if not passages:
        row.update(answer=NO_ANSWER, grounded=True, relevant=False, refused=True,
                   reason="retrieval found nothing", calls=0)
        return row
    answer = answer_fn(item["q"], passages)
    context = build_context(passages)  # the judge sees exactly what Claude saw
    verdict = parse_verdict(judge_fn(JUDGE_PROMPT.format(
        passages=context, question=item["q"], answer=answer)))
    row.update(answer=answer, calls=2, **verdict)
    return row


def summarize(rows: list[dict]) -> dict:
    answerable = [r for r in rows if r["expected"]]
    oos = [r for r in rows if not r["expected"]]

    def rate(items, key):
        return sum(1 for r in items if r[key]) / len(items) if items else float("nan")

    seen = [r for r in answerable if r["in_context"]]
    missed = [r for r in answerable if not r["in_context"]]
    return {
        "answerable": len(answerable),
        "in_context": len(seen),
        "answered_when_in_context": sum(1 for r in seen if not r["refused"]) / len(seen) if seen else float("nan"),
        "refused_when_missing": sum(1 for r in missed if r["refused"]) / len(missed) if missed else float("nan"),
        "grounded": rate(answerable, "grounded"),
        "relevant": rate(answerable, "relevant"),
        "out_of_scope": len(oos),
        "correct_refusal": rate(oos, "refused"),
        "refused_by_retrieval": sum(1 for r in oos if not r["retrieved"]),
        "refused_by_claude": sum(1 for r in oos if r["retrieved"] and r["refused"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sets", default="test,test_v2", help="comma separated")
    parser.add_argument("--limit", type=int, default=0, help="only the first N questions of each set")
    parser.add_argument("--dry-run", action="store_true", help="count calls, do not call the API")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--save", default="", help="write every graded case to this JSON file")
    args = parser.parse_args()

    items = []
    for name in args.sets.split(","):
        data = json.loads(Path(SETS[name.strip()]).read_text(encoding="utf-8"))
        items += data[: args.limit] if args.limit else data

    if args.dry_run:
        with_passages = sum(1 for i in items if search(i["q"], limit=5, synonyms=True))
        print(f"{len(items)} questions, {with_passages} reach Claude -> {2 * with_passages} API calls")
        print(f"models: answer={MODEL} judge={JUDGE_MODEL}")
        return 0

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("Set ANTHROPIC_API_KEY first (see --dry-run to count calls without a key).")
        return 2

    rows, errors = [], 0
    for n, item in enumerate(items, 1):
        try:
            row = grade_item(item)
        except Exception as exc:  # network, rate limit, bad judge reply
            errors += 1
            print(f"  [{n}/{len(items)}] ERROR {item['q']!r}: {exc}")
            continue
        rows.append(row)
        if args.verbose:
            kind = "ANSWERABLE" if row["expected"] else "OUT-OF-SCOPE"
            print(f"\n[{n}/{len(items)}] {kind}: {row['q']}\n  grounded={row['grounded']} "
                  f"relevant={row['relevant']} refused={row['refused']}  ({row['reason']})\n"
                  f"  answer: {row['answer'][:300]!r}")

    s = summarize(rows)
    print(f"\nanswer model={MODEL}  judge model={JUDGE_MODEL}  errors={errors}")
    print(f"answerable ({s['answerable']}): grounded {s['grounded']:.2f}  relevant {s['relevant']:.2f}")
    print(f"  the right article reached Claude in {s['in_context']} of {s['answerable']}; "
          f"it answered {s['answered_when_in_context']:.2f} of those, "
          f"and declined {s['refused_when_missing']:.2f} of the others")
    print(f"out of scope ({s['out_of_scope']}): correct refusal {s['correct_refusal']:.2f}  "
          f"(by retrieval {s['refused_by_retrieval']}, by Claude {s['refused_by_claude']})")
    if args.save:
        Path(args.save).write_text(json.dumps({"summary": s, "cases": rows}, indent=2), encoding="utf-8")
        print(f"saved to {args.save}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
