"""One-off loader: copy the local SQLite corpus into Neon/Postgres.

Run once for the SQLite -> Postgres cutover:
    .venv/bin/python scripts/migrate_to_neon.py [path/to/xbb.db]

Idempotent: truncates the tenant's tables first, then bulk-loads authors, posts, taxonomy,
assignments, embeddings (BLOB float32 -> pgvector), and sync_state (incl. the X OAuth token).
tenant_id is filled from the app.current_tenant DEFAULT bound by storage.connect.
"""

from __future__ import annotations

import sqlite3
import sys

import numpy as np
from dotenv import load_dotenv

from xbb import storage
from xbb.config import Config


def _batches(rows, n=1000):
    for i in range(0, len(rows), n):
        yield rows[i : i + n]


def _copy(pg, sql, rows, transform=None, label=""):
    total = 0
    for batch in _batches(rows):
        params = [transform(r) if transform else tuple(r) for r in batch]
        with pg.cursor() as cur:
            cur.executemany(sql, params)
        pg.commit()
        total += len(batch)
        print(f"  {label}: {total}/{len(rows)}", end="\r", flush=True)
    if rows:
        print()
    return total


def main() -> int:
    load_dotenv()
    db_path = sys.argv[1] if len(sys.argv) > 1 else "data/xbb.db"
    cfg = Config.from_env()
    if not cfg.database_url:
        print("DATABASE_URL not set", file=sys.stderr)
        return 2

    storage.init_db(cfg.database_url, cfg.tenant_id)
    lite = sqlite3.connect(db_path)
    pg = storage.connect(cfg.database_url, cfg.tenant_id)
    try:
        print("Truncating target tables ...")
        for t in ("assignments", "embeddings", "self_thread_posts", "posts",
                  "authors", "categories", "sync_state"):
            pg.execute(f"TRUNCATE {t} CASCADE")
        pg.commit()

        authors = lite.execute(
            "SELECT id, handle, display_name, avatar_url FROM authors").fetchall()
        _copy(pg,
              "INSERT INTO authors (id, handle, display_name, avatar_url) VALUES (%s,%s,%s,%s)",
              authors, label="authors")

        cols = ("id, url, text, lang, created_at, bookmarked_at, author_id, kind, "
                "parent_post_id, media_json, hashtags_json, links_json, like_count, "
                "repost_count, raw_json, bm_rank, label_attempted")
        posts = lite.execute(f"SELECT {cols} FROM posts").fetchall()
        _copy(pg, f"INSERT INTO posts ({cols}) VALUES ({','.join(['%s']*17)})",
              posts, label="posts")

        cats = lite.execute("SELECT id, name, definition, parent FROM categories").fetchall()
        _copy(pg, "INSERT INTO categories (id, name, definition, parent) VALUES (%s,%s,%s,%s)",
              cats, label="categories")
        # advance the identity sequence past the explicitly-inserted ids
        pg.execute("SELECT setval(pg_get_serial_sequence('categories','id'), "
                   "(SELECT MAX(id) FROM categories))")
        pg.commit()

        asg = lite.execute("SELECT post_id, category_id FROM assignments").fetchall()
        _copy(pg, "INSERT INTO assignments (post_id, category_id) VALUES (%s,%s) "
                  "ON CONFLICT DO NOTHING", asg, label="assignments")

        emb = lite.execute("SELECT post_id, vector FROM embeddings").fetchall()
        _copy(pg, "INSERT INTO embeddings (post_id, vector) VALUES (%s,%s)",
              emb, transform=lambda r: (r[0], np.frombuffer(r[1], dtype=np.float32)),
              label="embeddings")

        state = lite.execute("SELECT key, value FROM sync_state").fetchall()
        _copy(pg, "INSERT INTO sync_state (key, value) VALUES (%s,%s)", state, label="sync_state")

        print("\nRow counts on Neon:")
        for t in ("authors", "posts", "categories", "assignments", "embeddings", "sync_state"):
            n = pg.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            print(f"  {t:14} {n:>7}")
        size = pg.execute("SELECT pg_size_pretty(pg_database_size(current_database()))").fetchone()[0]
        print(f"  database size: {size}")
    finally:
        lite.close()
        pg.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
