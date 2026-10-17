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
    title    TEXT NOT NULL,
    heading  TEXT NOT NULL,
    content  TEXT NOT NULL,
    tsv      TSVECTOR GENERATED ALWAYS AS (
        setweight(to_tsvector('english', title), 'A') ||
        setweight(to_tsvector('english', heading), 'B') ||
        setweight(to_tsvector('english', content), 'C')
    ) STORED
);
CREATE INDEX IF NOT EXISTS chunks_tsv_idx ON chunks USING GIN (tsv);
"""


def connect() -> psycopg.Connection:
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


def init_schema() -> None:
    with connect() as conn:
        conn.execute(SCHEMA)
