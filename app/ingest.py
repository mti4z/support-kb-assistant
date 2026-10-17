"""Load the markdown articles in data/articles into PostgreSQL.

Each article is split into chunks, one per "## heading" section, so that search
returns the specific passage that answers a question instead of a whole page.
Running it again is safe: the table is emptied and reloaded.
"""
import re
from pathlib import Path

from app.db import connect, init_schema

ARTICLES_DIR = Path(__file__).resolve().parent.parent / "data" / "articles"


def parse_article(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8")
    meta = re.match(r"---\s*\ntitle:\s*(.+?)\s*\n---\s*\n", text)
    title = meta.group(1) if meta else path.stem
    body = text[meta.end():] if meta else text
    chunks = []
    for section in re.split(r"^## ", body, flags=re.MULTILINE):
        section = section.strip()
        if not section:
            continue
        heading, _, content = section.partition("\n")
        chunks.append(
            {
                "slug": path.stem,
                "title": title,
                "heading": heading.strip(),
                "content": content.strip(),
            }
        )
    return chunks


def ingest() -> int:
    init_schema()
    chunks = []
    for path in sorted(ARTICLES_DIR.glob("*.md")):
        chunks.extend(parse_article(path))
    with connect() as conn:
        conn.execute("TRUNCATE chunks RESTART IDENTITY")
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO chunks (slug, title, heading, content) "
                "VALUES (%(slug)s, %(title)s, %(heading)s, %(content)s)",
                chunks,
            )
    return len(chunks)


if __name__ == "__main__":
    print(f"Ingested {ingest()} chunks from {ARTICLES_DIR}")
