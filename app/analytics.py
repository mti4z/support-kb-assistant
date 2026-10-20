"""Log every question and report what the knowledge base is missing.

The most useful output for a support team is the list of questions nobody could
answer: each frequent one is an article that should be written.
"""
import logging

from app.db import connect

log = logging.getLogger(__name__)


def log_query(question: str, mode: str, passages: list[dict]) -> int | None:
    """Store a question and return its id. Never raises: logging must not break /ask."""
    top = passages[0] if passages else None
    try:
        with connect() as conn:
            row = conn.execute(
                "INSERT INTO query_log (question, mode, answered, top_slug, top_score) "
                "VALUES (%s, %s, %s, %s, %s) RETURNING id",
                (
                    question,
                    mode,
                    bool(passages),
                    top["slug"] if top else None,
                    float(top["score"]) if top else None,
                ),
            ).fetchone()
            return row["id"]
    except Exception:
        log.exception("could not log query")
        return None


def save_feedback(query_id: int, helpful: bool) -> bool:
    """Record "was this helpful?". Returns False if the question does not exist."""
    with connect() as conn:
        cur = conn.execute(
            "UPDATE query_log SET helpful = %s WHERE id = %s", (helpful, query_id)
        )
        return cur.rowcount == 1


def report(days: int = 30) -> dict:
    """Summary of the last `days` days."""
    since = "asked_at >= now() - make_interval(days => %(days)s)"
    with connect() as conn:
        totals = conn.execute(
            f"""
            SELECT count(*)                                  AS questions,
                   count(*) FILTER (WHERE answered)          AS answered,
                   count(helpful)                            AS rated,
                   count(*) FILTER (WHERE helpful)           AS helpful_yes
            FROM query_log WHERE {since}
            """,
            {"days": days},
        ).fetchone()

        unanswered = conn.execute(
            f"""
            SELECT lower(trim(question)) AS question, count(*) AS times,
                   max(asked_at) AS last_asked
            FROM query_log WHERE NOT answered AND {since}
            GROUP BY 1 ORDER BY times DESC, last_asked DESC LIMIT 20
            """,
            {"days": days},
        ).fetchall()

        not_helpful = conn.execute(
            f"""
            SELECT top_slug AS article, count(*) AS times
            FROM query_log WHERE answered AND helpful = false AND {since}
            GROUP BY 1 ORDER BY times DESC LIMIT 10
            """,
            {"days": days},
        ).fetchall()

        top_articles = conn.execute(
            f"""
            SELECT top_slug AS article, count(*) AS times
            FROM query_log WHERE answered AND {since}
            GROUP BY 1 ORDER BY times DESC LIMIT 10
            """,
            {"days": days},
        ).fetchall()

    n = totals["questions"]
    rated = totals["rated"]
    return {
        "days": days,
        "questions": n,
        "answer_rate": round(totals["answered"] / n, 3) if n else None,
        "rated": rated,
        "helpful_rate": round(totals["helpful_yes"] / rated, 3) if rated else None,
        "unanswered_questions": unanswered,
        "articles_rated_not_helpful": not_helpful,
        "most_used_articles": top_articles,
    }
