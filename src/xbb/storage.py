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
    display_name  TEXT,
    avatar_url    TEXT                -- profile image URL (pbs.twimg.com), nullable
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
    raw_json       TEXT,               -- original X payload, retained verbatim
    bm_rank        INTEGER,            -- bookmark-recency rank; higher = more recently saved
    label_attempted INTEGER            -- 1 once labeling has been tried (avoid retrying every sync)
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
    definition TEXT,
    parent     TEXT                -- top-level grouping for the category tree, nullable
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

CREATE TABLE IF NOT EXISTS sync_state (
    key   TEXT PRIMARY KEY,   -- e.g. 'bookmarks_cursor'
    value TEXT
);
"""


def connect(db_path: str) -> sqlite3.Connection:
    """Open a connection with foreign keys enabled."""
    con = sqlite3.connect(db_path)
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA journal_mode = WAL")     # readers don't block the refill writer
    con.execute("PRAGMA busy_timeout = 5000")  # wait, don't error, if a refill is writing
    return con


def init_db(db_path: str) -> None:
    """Create all tables (and the vector store) if absent. Safe to run repeatedly."""
    parent = Path(db_path).expanduser().parent
    if parent and not parent.exists():
        parent.mkdir(parents=True, exist_ok=True)
    con = connect(db_path)
    try:
        con.executescript(SCHEMA)
        # Migrations: add columns to databases created before these features.
        cat_cols = [r[1] for r in con.execute("PRAGMA table_info(categories)")]
        if "parent" not in cat_cols:
            con.execute("ALTER TABLE categories ADD COLUMN parent TEXT")
        author_cols = [r[1] for r in con.execute("PRAGMA table_info(authors)")]
        if "avatar_url" not in author_cols:
            con.execute("ALTER TABLE authors ADD COLUMN avatar_url TEXT")
        post_cols = [r[1] for r in con.execute("PRAGMA table_info(posts)")]
        if "bm_rank" not in post_cols:
            con.execute("ALTER TABLE posts ADD COLUMN bm_rank INTEGER")
        if "label_attempted" not in post_cols:
            con.execute("ALTER TABLE posts ADD COLUMN label_attempted INTEGER")
        con.commit()
    finally:
        con.close()


def get_state(con: sqlite3.Connection, key: str) -> str | None:
    """Read an arbitrary value from the sync_state key/value store (e.g. OAuth tokens JSON)."""
    row = con.execute("SELECT value FROM sync_state WHERE key = ?", (key,)).fetchone()
    return row[0] if row and row[0] else None


def set_state(con: sqlite3.Connection, key: str, value: str | None) -> None:
    """Write/clear an arbitrary sync_state value."""
    if value is None:
        con.execute("DELETE FROM sync_state WHERE key = ?", (key,))
    else:
        con.execute(
            "INSERT INTO sync_state (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
    con.commit()


def get_sync_cursor(con: sqlite3.Connection) -> str | None:
    """The saved bookmarks pagination cursor to resume from, or None (never started / done)."""
    row = con.execute("SELECT value FROM sync_state WHERE key = 'bookmarks_cursor'").fetchone()
    return row[0] if row and row[0] else None


def set_sync_cursor(con: sqlite3.Connection, cursor: str | None) -> None:
    """Persist where the next backfill should resume. None clears it (the sync finished)."""
    if cursor:
        con.execute(
            "INSERT INTO sync_state (key, value) VALUES ('bookmarks_cursor', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (cursor,),
        )
    else:
        con.execute("DELETE FROM sync_state WHERE key = 'bookmarks_cursor'")
    con.commit()
