"""Load the markdown articles in data/articles into PostgreSQL.

Each article is split into chunks, one per "## heading" section, so that search
returns the specific passage that answers a question instead of a whole page.
Running it again is safe: the table is emptied and reloaded.
"""
import re
from pathlib import Path

from app.db import connect, init_schema
from app.lang import TS_CONFIG, strip_accents

DATA = Path(__file__).resolve().parent.parent / "data"
ARTICLES_DIR = DATA / "articles"
SYNONYMS_FILE = DATA / "synonyms.txt"
# (language, articles folder, synonyms file). The Spanish articles keep the same
# file names, so an article has the same slug in both languages.
SOURCES = [
    ("en", ARTICLES_DIR, SYNONYMS_FILE),
    ("es", DATA / "articles_es", DATA / "synonyms_es.txt"),
]


def normalize_term(term: str) -> str:
    """Lowercase, drop accents, punctuation to spaces: "Wi-Fi" -> "wi fi"."""
    return re.sub(r"[^a-z0-9]+", " ", strip_accents(term).lower()).strip()


def parse_synonyms(path: Path, lang: str = "en") -> list[dict]:
    rows = []
    group_id = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        group_id += 1
        for term in dict.fromkeys(normalize_term(t) for t in line.split(",")):
            if term:
                rows.append({"group_id": group_id, "term": term, "lang": lang})
    return rows


def parse_article(path: Path, lang: str = "en") -> list[dict]:
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
                "lang": lang,
                "title": title,
                "heading": heading.strip(),
                "content": content.strip(),
            }
        )
    return chunks


INSERT_CHUNK = """
INSERT INTO chunks (slug, lang, title, heading, content, tsv)
VALUES (
    %(slug)s, %(lang)s, %(title)s, %(heading)s, %(content)s,
    setweight(to_tsvector(%(config)s::regconfig, %(t)s), 'A') ||
    setweight(to_tsvector(%(config)s::regconfig, %(h)s), 'B') ||
    setweight(to_tsvector(%(config)s::regconfig, %(c)s), 'C')
)
"""


def _chunk_params(chunk: dict) -> dict:
    """Spanish text is searched without accents (people often type without them)."""
    fold = strip_accents if chunk["lang"] == "es" else (lambda text: text)
    return {
        **chunk,
        "config": TS_CONFIG[chunk["lang"]],
        "t": fold(chunk["title"]),
        "h": fold(chunk["heading"]),
        "c": fold(chunk["content"]),
    }


def ingest() -> int:
    init_schema()
    chunks, synonyms = [], []
    for lang, folder, syn_file in SOURCES:
        if folder.exists():
            for path in sorted(folder.glob("*.md")):
                chunks.extend(parse_article(path, lang))
        if syn_file.exists():
            synonyms.extend(parse_synonyms(syn_file, lang))
    with connect() as conn:
        conn.execute("TRUNCATE chunks RESTART IDENTITY")
        conn.execute("TRUNCATE synonyms RESTART IDENTITY")
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO synonyms (group_id, term, lang) VALUES (%(group_id)s, %(term)s, %(lang)s)",
                synonyms,
            )
        with conn.cursor() as cur:
            cur.executemany(INSERT_CHUNK, [_chunk_params(c) for c in chunks])
    return len(chunks)


if __name__ == "__main__":
    print(f"Ingested {ingest()} chunks from {DATA}")
