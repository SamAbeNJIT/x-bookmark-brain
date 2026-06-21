"""Local persistence (SQLite) for bookmarks, authors, taxonomy, and assignments.

The schema encodes the data decisions from docs/PRD.md. The `embeddings` table is the
vector store; wiring an ANN index over it (e.g. sqlite-vec) lands in the semantic-search
slice (#4). Posts are keyed by X post id so backfill upserts are idempotent.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS authors (
    id            TEXT PRIMARY KEY,
    handle        TEXT,
    display_name  TEXT
);

CREATE TABLE IF NOT EXISTS posts (
    id             TEXT PRIMARY KEY,   -- X post id; upsert target for idempotent backfill
    url            TEXT,
    text           TEXT,
    lang           TEXT,
    created_at     TEXT,
    bookmarked_at  TEXT,
    author_id      TEXT REFERENCES authors(id),
    kind           TEXT,               -- 'original' | 'reply' | 'quote'
    parent_post_id TEXT,               -- parent (reply) or quoted (quote) post, nullable
    media_json     TEXT,               -- [{url, alt_text, type}]
    hashtags_json  TEXT,
    links_json     TEXT,
    like_count     INTEGER,
    repost_count   INTEGER,
    raw_json       TEXT                -- original X payload, retained verbatim
);

CREATE TABLE IF NOT EXISTS self_thread_posts (
    root_post_id TEXT REFERENCES posts(id),
    position     INTEGER,
    post_id      TEXT REFERENCES posts(id),
    PRIMARY KEY (root_post_id, position)
);

CREATE TABLE IF NOT EXISTS categories (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT UNIQUE,
    definition TEXT
);

CREATE TABLE IF NOT EXISTS assignments (
    post_id     TEXT REFERENCES posts(id),
    category_id INTEGER REFERENCES categories(id),
    PRIMARY KEY (post_id, category_id)   -- multi-label, one row per (post, category)
);

CREATE TABLE IF NOT EXISTS embeddings (
    post_id TEXT PRIMARY KEY REFERENCES posts(id),
    vector  BLOB
);
"""


def connect(db_path: str) -> sqlite3.Connection:
    """Open a connection with foreign keys enabled."""
    con = sqlite3.connect(db_path)
    con.execute("PRAGMA foreign_keys = ON")
    return con


def init_db(db_path: str) -> None:
    """Create all tables (and the vector store) if absent. Safe to run repeatedly."""
    parent = Path(db_path).expanduser().parent
    if parent and not parent.exists():
        parent.mkdir(parents=True, exist_ok=True)
    con = connect(db_path)
    try:
        con.executescript(SCHEMA)
        con.commit()
    finally:
        con.close()
