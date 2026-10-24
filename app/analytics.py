"""Log every question and report what the knowledge base is missing.

The most useful output for a support team is the list of questions nobody could
answer: each frequent one is an article that should be written.
"""
import logging
import os

from app.db import connect

log = logging.getLogger(__name__)


def log_query(
    question: str, mode: str, passages: list[dict], tenant_id: int | None = None
) -> int | None:
    """Store a question and return its id. Never raises: logging must not break /ask."""
    top = passages[0] if passages else None
    try:
        with connect() as conn:
            row = conn.execute(
                "INSERT INTO query_log (question, mode, answered, top_slug, top_score, tenant_id) "
                "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                (
                    question,
                    mode,
                    bool(passages),
                    top["slug"] if top else None,
                    float(top["score"]) if top else None,
                    tenant_id,
                ),
            ).fetchone()
            return row["id"]
    except Exception:
        log.exception("could not log query")
        return None


def save_feedback(query_id: int, helpful: bool, tenant_id: int | None = None) -> bool:
    """Record "was this helpful?". Returns False if the question does not exist."""
    with connect() as conn:
        cur = conn.execute(
            "UPDATE query_log SET helpful = %s "
            "WHERE id = %s AND tenant_id IS NOT DISTINCT FROM %s",
            (helpful, query_id, tenant_id),
        )
        return cur.rowcount == 1


def report(days: int = 30, tenant_id: int | None = None) -> dict:
    """Summary of the last `days` days."""
    since = (
        "asked_at >= now() - make_interval(days => %(days)s) "
        "AND tenant_id IS NOT DISTINCT FROM %(tenant)s"
    )
    with connect() as conn:
        totals = conn.execute(
            f"""
            SELECT count(*)                                  AS questions,
                   count(*) FILTER (WHERE answered)          AS answered,
                   count(helpful)                            AS rated,
                   count(*) FILTER (WHERE helpful)           AS helpful_yes
            FROM query_log WHERE {since}
            """,
            {"days": days, "tenant": tenant_id},
        ).fetchone()

        unanswered = conn.execute(
            f"""
            SELECT lower(trim(question)) AS question, count(*) AS times,
                   max(asked_at) AS last_asked
            FROM query_log WHERE NOT answered AND {since}
            GROUP BY 1 ORDER BY times DESC, last_asked DESC LIMIT 20
            """,
            {"days": days, "tenant": tenant_id},
        ).fetchall()

        not_helpful = conn.execute(
            f"""
            SELECT top_slug AS article, count(*) AS times
            FROM query_log WHERE answered AND helpful = false AND {since}
            GROUP BY 1 ORDER BY times DESC LIMIT 10
            """,
            {"days": days, "tenant": tenant_id},
        ).fetchall()

        top_articles = conn.execute(
            f"""
            SELECT top_slug AS article, count(*) AS times
            FROM query_log WHERE answered AND {since}
            GROUP BY 1 ORDER BY times DESC LIMIT 10
            """,
            {"days": days, "tenant": tenant_id},
        ).fetchall()

    to_review = documents_to_review(days, tenant_id)

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
        "documents_to_review": to_review,
    }


def documents_to_review(days: int = 30, tenant_id: int | None = None) -> list[dict]:
    """Documents that probably need attention, most urgent first.

    Reasons (a document can have several):
      rated_not_helpful  at least 2 "not helpful" votes, and more than "helpful" ones
      stale              uploaded STALE_DAYS (default 180) or more days ago (clients only)
      unused             older than the report window and never the best match in it (clients only)
    """
    stale_days = int(os.environ.get("STALE_DAYS", "180"))
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT c.slug, min(c.title) AS title,
                   extract(epoch FROM now() - max(c.created_at)) / 86400 AS age_days,
                   coalesce(u.used, 0) AS used,
                   coalesce(u.not_helpful, 0) AS not_helpful,
                   coalesce(u.helpful, 0) AS helpful
            FROM chunks c
            LEFT JOIN (
                SELECT top_slug, count(*) AS used,
                       count(*) FILTER (WHERE helpful = false) AS not_helpful,
                       count(*) FILTER (WHERE helpful) AS helpful
                FROM query_log
                WHERE answered
                  AND asked_at >= now() - make_interval(days => %(days)s)
                  AND tenant_id IS NOT DISTINCT FROM %(tenant)s
                GROUP BY top_slug
            ) u ON u.top_slug = c.slug
            WHERE c.tenant_id IS NOT DISTINCT FROM %(tenant)s
            GROUP BY c.slug, u.used, u.not_helpful, u.helpful
            """,
            {"days": days, "tenant": tenant_id},
        ).fetchall()

    out = []
    for r in rows:
        reasons = []
        if r["not_helpful"] >= 2 and r["not_helpful"] > r["helpful"]:
            reasons.append("rated_not_helpful")
        if tenant_id is not None and r["age_days"] >= stale_days:
            reasons.append("stale")
        if tenant_id is not None and r["used"] == 0 and r["age_days"] >= days:
            reasons.append("unused")
        if reasons:
            out.append(
                {
                    "slug": r["slug"],
                    "title": r["title"],
                    "reasons": reasons,
                    "times_used": r["used"],
                    "rated_not_helpful": r["not_helpful"],
                    "rated_helpful": r["helpful"],
                    "age_days": int(r["age_days"]),
                }
            )
    out.sort(key=lambda d: (-len(d["reasons"]), -d["rated_not_helpful"], -d["age_days"]))
    return out[:20]
