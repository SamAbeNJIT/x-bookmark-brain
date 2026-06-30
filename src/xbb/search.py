"""Semantic search: embed posts (batched, resumable) and rank with pgvector cosine.

Vectors live in the ``embeddings`` table as pgvector ``vector(1024)`` with an HNSW cosine
index. Search is a single ``ORDER BY vector <=> query`` — the database does the ranking and
returns only the top-k, so nothing loads the whole corpus into memory. The connection must
come from ``storage.connect`` (which registers the pgvector type + binds the tenant).
"""

from __future__ import annotations

from typing import Any, Callable

import numpy as np
import psycopg

from .ai import AIClient


def _unindexed(con: psycopg.Connection) -> list[tuple[str, str]]:
    return con.execute(
        """
        SELECT p.id, p.text
        FROM posts p
        LEFT JOIN embeddings e ON e.tenant_id = p.tenant_id AND e.post_id = p.id
        WHERE e.post_id IS NULL AND p.text IS NOT NULL AND p.text <> ''
        """
    ).fetchall()


def index_posts(
    con: psycopg.Connection,
    ai: AIClient,
    batch_size: int = 100,
    progress: Callable[[int, int], None] | None = None,
) -> int:
    """Embed posts that don't yet have an embedding, in batches.

    Resumable and interrupt-safe: only un-embedded posts are processed, and each batch is
    committed before the next, so a crash or Ctrl-C loses at most one batch's work. Returns
    the number newly embedded.
    """
    rows = _unindexed(con)
    total = len(rows)
    done = 0
    for start in range(0, total, batch_size):
        chunk = rows[start : start + batch_size]
        vectors = ai.embed([text for _, text in chunk])
        params = [
            (post_id, np.asarray(vec, dtype=np.float32))
            for (post_id, _), vec in zip(chunk, vectors)
        ]
        with con.cursor() as cur:
            cur.executemany(
                "INSERT INTO embeddings (post_id, vector) VALUES (%s, %s) "
                "ON CONFLICT (tenant_id, post_id) DO UPDATE SET vector = excluded.vector",
                params,
            )
        con.commit()
        done += len(chunk)
        if progress is not None:
            progress(done, total)
    return done


def search(con: psycopg.Connection, ai: AIClient, query: str, k: int = 10) -> list[dict[str, Any]]:
    """Return up to k posts ranked by cosine similarity to the query (pgvector HNSW)."""
    q = np.asarray(ai.embed([query])[0], dtype=np.float32)
    rows = con.execute(
        """
        SELECT p.id, p.url, p.text, a.handle, a.avatar_url, p.media_json, c.parent,
               e.vector <=> %s AS dist
        FROM embeddings e
        JOIN posts p ON p.tenant_id = e.tenant_id AND p.id = e.post_id
        LEFT JOIN authors a ON a.tenant_id = p.tenant_id AND a.id = p.author_id
        LEFT JOIN LATERAL (
            SELECT category_id AS cid
            FROM assignments
            WHERE tenant_id = p.tenant_id AND post_id = p.id
            ORDER BY category_id
            LIMIT 1
        ) pa ON true
        LEFT JOIN categories c ON c.id = pa.cid
        ORDER BY dist
        LIMIT %s
        """,
        (q, k),
    ).fetchall()
    return [
        {"id": r[0], "url": r[1], "text": r[2], "handle": r[3], "avatar_url": r[4],
         "media_json": r[5], "parent": r[6], "score": 1.0 - float(r[7])}
        for r in rows
    ]
