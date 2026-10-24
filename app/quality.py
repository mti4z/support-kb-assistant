"""Quality check on a client's own documents.

Takes a sample of the client's chunks, writes one question for each (as a user
would ask it), runs the normal search and reports how often the right document
comes back. With ANTHROPIC_API_KEY the questions are written by Claude, in the
language of the chunk and without copying its words. Without it, the section
heading is used as the question, which is an optimistic estimate and is
reported as such.

Limits: the same model family writes the questions, so this is a signal for
finding documents that are hard to retrieve, not an audit of answer accuracy.
"""
import os
import random

from app.answer import message_text
from app.db import connect
from app.search import search

SAMPLE = 20
PROMPT = {
    "en": "Write ONE short question that a person who has never seen this document would type to find it. "
    "Do not copy the heading or whole phrases from the text. Reply with the question only.",
    "es": "Escribí UNA pregunta corta que haría una persona que nunca vio este documento para encontrarlo. "
    "No copies el título ni frases enteras del texto. Respondé solo con la pregunta.",
}


def _question_with_claude(heading: str, content: str, lang: str) -> str:
    import anthropic

    msg = anthropic.Anthropic().messages.create(
        model=os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5"),
        max_tokens=100,
        system=PROMPT.get(lang, PROMPT["en"]),
        messages=[{"role": "user", "content": f"{heading}\n\n{content[:1500]}"}],
    )
    return message_text(msg).strip().strip('"')


def make_question(chunk: dict) -> tuple[str, str]:
    """Return (question, method)."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            q = _question_with_claude(chunk["heading"], chunk["content"], chunk["lang"])
            if q:
                return q, "claude"
        except Exception:
            pass
    return chunk["heading"], "headings"


def run_quality(tenant_id: int, sample: int = SAMPLE, seed: int | None = None) -> dict:
    with connect() as conn:
        chunks = conn.execute(
            "SELECT slug, lang, title, heading, content FROM chunks "
            "WHERE tenant_id = %s ORDER BY id",
            (tenant_id,),
        ).fetchall()
    if not chunks:
        return {"tested": 0, "method": None, "top1": None, "top3": None, "missed": []}
    picked = random.Random(seed).sample(chunks, min(sample, len(chunks)))

    top1 = top3 = 0
    missed, methods = [], set()
    for c in picked:
        question, method = make_question(c)
        methods.add(method)
        slugs = [r["slug"] for r in search(question, limit=3, tenant_id=tenant_id)]
        if slugs[:1] == [c["slug"]]:
            top1 += 1
        if c["slug"] in slugs:
            top3 += 1
        else:
            missed.append({"slug": c["slug"], "title": c["title"], "heading": c["heading"], "question": question})
    n = len(picked)
    return {
        "tested": n,
        "method": "claude" if methods == {"claude"} else "headings" if methods == {"headings"} else "mixed",
        "top1": round(top1 / n, 3),
        "top3": round(top3 / n, 3),
        "missed": missed[:10],
    }
