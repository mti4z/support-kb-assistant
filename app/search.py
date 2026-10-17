"""Retrieval with PostgreSQL full-text search.

Questions are written in natural language, so requiring every word to match
(the default AND behaviour) would return nothing. We turn the question into an
OR query over the stemmed keywords and let ts_rank_cd order the passages. Title
matches weigh more than heading matches, which weigh more than body matches.
"""
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


def search(question: str, limit: int = 5, min_score: float = MIN_SCORE) -> list[dict]:
    if not question.strip():
        return []
    with connect() as conn:
        rows = conn.execute(SEARCH_SQL, {"question": question, "limit": limit}).fetchall()
    return [r for r in rows if r["score"] >= min_score]
