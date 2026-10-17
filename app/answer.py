"""Turn retrieved passages into an answer.

Two modes:
  * extractive (default): returns the best passage. Needs no API key.
  * claude: if ANTHROPIC_API_KEY is set, Claude writes a short answer using ONLY
    the retrieved passages and must say so when they do not contain the answer.
If the API call fails for any reason we fall back to the extractive answer.
"""
import os

NO_ANSWER = (
    "I could not find an article that answers this. "
    "Please open a ticket with the service desk."
)

MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5")

SYSTEM_PROMPT = (
    "You are an IT support assistant. Answer the user's question using ONLY the "
    "passages provided. Be concise and give steps as a short list when useful. "
    "Mention the article title(s) you used. If the passages do not contain the "
    "answer, say you don't know and suggest opening a ticket."
)


def _extractive(passages: list[dict]) -> str:
    top = passages[0]
    return f"{top['heading']}: {top['content']}"


def _with_claude(question: str, passages: list[dict]) -> str:
    import anthropic

    context = "\n\n".join(
        f"[{p['title']} / {p['heading']}]\n{p['content']}" for p in passages[:3]
    )
    client = anthropic.Anthropic()
    msg = client.messages.create(
        model=MODEL,
        max_tokens=500,
        system=SYSTEM_PROMPT,
        messages=[
            {"role": "user", "content": f"Passages:\n{context}\n\nQuestion: {question}"}
        ],
    )
    return msg.content[0].text


def build_answer(question: str, passages: list[dict]) -> tuple[str, str]:
    """Return (answer, mode)."""
    if not passages:
        return NO_ANSWER, "no_match"
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            return _with_claude(question, passages), "claude"
        except Exception:
            pass
    return _extractive(passages), "extractive"
