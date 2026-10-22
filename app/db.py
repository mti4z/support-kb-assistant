import os

import psycopg
from psycopg.rows import dict_row

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/supportkb"
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS chunks (
    id       SERIAL PRIMARY KEY,
    slug     TEXT NOT NULL,
    lang     TEXT NOT NULL DEFAULT 'en',
    title    TEXT NOT NULL,
    heading  TEXT NOT NULL,
    content  TEXT NOT NULL,
    tsv      TSVECTOR,         -- filled by app/ingest.py with the stemmer of the article's language
    tenant_id INT              -- NULL = the public demo knowledge base; otherwise one client's document
);
CREATE INDEX IF NOT EXISTS chunks_tsv_idx ON chunks USING GIN (tsv);

-- One row per question asked, so we can see what people need and what is missing.
CREATE TABLE IF NOT EXISTS query_log (
    id        SERIAL PRIMARY KEY,
    asked_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    question  TEXT NOT NULL,
    mode      TEXT NOT NULL,        -- extractive, claude or no_match
    answered  BOOLEAN NOT NULL,     -- false when no article matched
    top_slug  TEXT,                 -- article of the best passage
    top_score REAL,
    helpful   BOOLEAN               -- NULL until the user answers "was this helpful?"
);
CREATE INDEX IF NOT EXISTS query_log_asked_at_idx ON query_log (asked_at DESC);

-- One row per client. Only the SHA-256 of the API key is stored, never the key.
CREATE TABLE IF NOT EXISTS tenants (
    id           SERIAL PRIMARY KEY,
    name         TEXT NOT NULL,
    api_key_hash TEXT NOT NULL UNIQUE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Words that mean the same thing in IT support (loaded from data/synonyms.txt).
CREATE TABLE IF NOT EXISTS synonyms (
    id       SERIAL PRIMARY KEY,
    group_id INT  NOT NULL,
    term     TEXT NOT NULL,  -- normalized: lowercase letters and digits, single spaces
    lang     TEXT NOT NULL DEFAULT 'en'
);
"""


def connect() -> psycopg.Connection:
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


def init_schema() -> None:
    with connect() as conn:
        # Databases created before the Spanish support have no chunks.lang. The
        # table is reloaded by ingest anyway, so it is dropped and recreated.
        has_lang = conn.execute(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = 'chunks' AND column_name = 'lang'"
        ).fetchone()
        exists = conn.execute("SELECT to_regclass('chunks') AS t").fetchone()["t"]
        if exists and not has_lang:
            conn.execute("DROP TABLE chunks")
        conn.execute(SCHEMA)
        conn.execute("ALTER TABLE synonyms ADD COLUMN IF NOT EXISTS lang TEXT NOT NULL DEFAULT 'en'")
        # Client separation (added after the first release): NULL = public demo data.
        conn.execute("ALTER TABLE chunks ADD COLUMN IF NOT EXISTS tenant_id INT")
        conn.execute("ALTER TABLE query_log ADD COLUMN IF NOT EXISTS tenant_id INT")
        conn.execute("CREATE INDEX IF NOT EXISTS chunks_tenant_slug_idx ON chunks (tenant_id, slug)")
        conn.execute("CREATE INDEX IF NOT EXISTS query_log_tenant_idx ON query_log (tenant_id)")
        # Public widget key (can only ask questions) and the sites allowed to embed it.
        conn.execute("ALTER TABLE tenants ADD COLUMN IF NOT EXISTS widget_token TEXT")
        conn.execute("ALTER TABLE tenants ADD COLUMN IF NOT EXISTS allowed_origins TEXT[] NOT NULL DEFAULT '{}'")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS tenants_widget_token_idx ON tenants (widget_token)")
