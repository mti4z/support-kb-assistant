"""Turn retrieved passages into an answer.

Two modes:
  * extractive (default): returns the best passage. Needs no API key.
  * claude: if ANTHROPIC_API_KEY is set, Claude writes a short answer using ONLY
    the retrieved passages and must say so when they do not contain the answer.
If the API call fails for any reason we fall back to the extractive answer.
"""
import os
import re

from app.lang import detect_language

NO_ANSWERS = {
    "en": "I could not find an article that answers this. Please open a ticket with the service desk.",
    "es": "No encontré un artículo que responda esto. Por favor abrí un ticket con el servicio de soporte.",
}
NO_ANSWER = NO_ANSWERS["en"]  # kept for code that imports the English text

MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5")

SYSTEM_PROMPT = (
    "You are an IT support assistant. Answer the user's question using ONLY the "
    "passages provided. Be concise and give steps as a short list when useful. "
    "Mention the article title(s) you used. If the passages do not contain the "
    "answer, say you don't know and suggest opening a ticket. "
    "Reply in the same language as the user's question. "
    "Write plain text only: no Markdown, no asterisks, no headings, no tables; "
    "number steps as '1.', '2.' on separate lines."
)
VOSEO = (
    " When you reply in Spanish, use the Argentine voseo (vos, probá, fijate, abrí), "
    "never the tuteo (tú, prueba, abre)."
)


def system_prompt() -> str:
    """ES_VOSEO=0 turns the Argentine voseo off (neutral Spanish)."""
    return SYSTEM_PROMPT + (VOSEO if os.environ.get("ES_VOSEO", "1") != "0" else "")


def plain_text(text: str) -> str:
    """Remove the Markdown marks the page cannot render (it shows text as is)."""
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text, flags=re.S)
    text = re.sub(r"(?m)^\s{0,3}#{1,6}\s+", "", text)
    text = re.sub(r"(?m)^(\s*)[*-]\s+", r"\1- ", text)
    return text.replace("`", "")


def message_text(msg) -> str:
    """Join the text blocks of a reply. The first block may be a thinking block."""
    text = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text")
    if not text.strip():
        raise ValueError("the reply had no text block")
    return text


def _extractive(passages: list[dict]) -> str:
    top = passages[0]
    return f"{top['heading']}: {top['content']}"


def _format(items: list[dict]) -> str:
    return "\n\n".join(f"[{p['title']} / {p['heading']}]\n{p['content']}" for p in items)


def build_context(passages: list[dict], max_articles: int = 2) -> str:
    """Text given to Claude.

    Default: every section of the best `max_articles` articles, because the best
    passage can come from the wrong section of the right article. Set
    ANSWER_CONTEXT=passages to use only the 3 best passages instead.
    """
    if os.environ.get("ANSWER_CONTEXT", "article") == "passages":
        return _format(passages[:3])
    lang = passages[0].get("lang", "en")
    tenant_id = passages[0].get("tenant_id")
    slugs: list[str] = []
    for p in passages:
        if p["slug"] not in slugs:
            slugs.append(p["slug"])
    try:
        from app.db import connect

        items = []
        with connect() as conn:
            for slug in slugs[:max_articles]:
                items += conn.execute(
                    "SELECT title, heading, content FROM chunks "
                    "WHERE slug = %s AND lang = %s AND tenant_id IS NOT DISTINCT FROM %s "
                    "ORDER BY id",
                    (slug, lang, tenant_id),
                ).fetchall()
        if items:
            return _format(items)
    except Exception:
        pass
    return _format(passages[:3])


def _with_claude(question: str, passages: list[dict]) -> str:
    import anthropic

    context = build_context(passages)
    client = anthropic.Anthropic()
    msg = client.messages.create(
        model=MODEL,
        max_tokens=1500,
        system=system_prompt(),
        messages=[
            {"role": "user", "content": f"Passages:\n{context}\n\nQuestion: {question}"}
        ],
    )
    return plain_text(message_text(msg))


def build_answer(question: str, passages: list[dict]) -> tuple[str, str]:
    """Return (answer, mode)."""
    if not passages:
        return NO_ANSWERS[detect_language(question)], "no_match"
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            return _with_claude(question, passages), "claude"
        except Exception:
            pass
    return _extractive(passages), "extractive"
