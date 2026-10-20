"""Retrieval with PostgreSQL full-text search.

Questions are written in natural language, so requiring every word to match
(the default AND behaviour) would return nothing. We turn the question into an
OR query over the stemmed keywords and let ts_rank_cd order the passages. Title
matches weigh more than heading matches, which weigh more than body matches.

Before searching, the question can be expanded with synonyms (see
data/synonyms.txt): "my PC is slow" also searches for "computer", "laptop", etc.
Set USE_SYNONYMS=0 to turn this off.
"""
import os
import re

from app.db import connect

# Below this normalized score we treat the question as "not covered by the
# knowledge base" instead of returning a weak guess. Tuned with eval/run_eval.py.
MIN_SCORE = 0.2

SEARCH_SQL = """
WITH q AS (
    SELECT to_tsquery('english',
        NULLIF(replace(plainto_tsquery('english', %(question)s)::text, '&', '|'), '')
    ) AS query
)
SELECT c.slug, c.title, c.heading, c.content,
       ts_rank_cd(c.tsv, q.query, 32) AS score
FROM chunks c, q
WHERE c.tsv @@ q.query
ORDER BY score DESC
LIMIT %(limit)s
"""


def synonyms_enabled() -> bool:
    return os.environ.get("USE_SYNONYMS", "1") != "0"


def expand_query(question: str, conn) -> str:
    """Add the synonyms of every known word or phrase found in the question."""
    text = " " + re.sub(r"[^a-z0-9]+", " ", question.lower()).strip() + " "
    groups: dict[int, list[str]] = {}
    for row in conn.execute("SELECT group_id, term FROM synonyms").fetchall():
        groups.setdefault(row["group_id"], []).append(row["term"])

    extra: list[str] = []
    for terms in groups.values():
        # " term " also matches a plural: "printers" counts as "printer"
        if any(re.search(rf" {re.escape(t)}(?:s|es)? ", text) for t in terms):
            extra += [t for t in terms if f" {t} " not in text]
    return f"{question} {' '.join(extra)}" if extra else question


def search(
    question: str,
    limit: int = 5,
    min_score: float = MIN_SCORE,
    synonyms: bool | None = None,
) -> list[dict]:
    if not question.strip():
        return []
    use_synonyms = synonyms_enabled() if synonyms is None else synonyms
    with connect() as conn:
        query_text = expand_query(question, conn) if use_synonyms else question
        rows = conn.execute(SEARCH_SQL, {"question": query_text, "limit": limit}).fetchall()
    return [r for r in rows if r["score"] >= min_score]
