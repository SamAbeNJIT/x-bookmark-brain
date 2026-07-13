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


def posts_by_ids(con: psycopg.Connection, ids: list[str]) -> list[dict[str, Any]]:
    """Hydrate posts by id, in the given order — same card shape `search` returns (minus score).

    Used by the Ask UI to re-render earlier turns' source bookmarks: the thread state lives
    client-side (hidden form field carries only the ids), so each stateless re-render fetches
    the cards fresh. Unknown ids are silently dropped."""
    if not ids:
        return []
    rows = con.execute(
        """
        SELECT p.id, p.url, p.text, a.handle, a.avatar_url, p.media_json, c.parent
        FROM posts p
        LEFT JOIN authors a ON a.tenant_id = p.tenant_id AND a.id = p.author_id
        LEFT JOIN LATERAL (
            SELECT category_id AS cid
            FROM assignments
            WHERE tenant_id = p.tenant_id AND post_id = p.id
            ORDER BY category_id
            LIMIT 1
        ) pa ON true
        LEFT JOIN categories c ON c.id = pa.cid
        WHERE p.id = ANY(%s)
        """,
        (ids,),
    ).fetchall()
    by_id = {
        r[0]: {"id": r[0], "url": r[1], "text": r[2], "handle": r[3], "avatar_url": r[4],
               "media_json": r[5], "parent": r[6]}
        for r in rows
    }
    return [by_id[i] for i in ids if i in by_id]


def search(con: psycopg.Connection, ai: AIClient, query: str, k: int = 10) -> list[dict[str, Any]]:
    """Hybrid retrieval: semantic (pgvector HNSW) + lexical (Postgres FTS), fused with RRF.

    The two legs each rank their top candidates; Reciprocal Rank Fusion (k=60) merges them —
    rank-based, so the incomparable scores (cosine distance vs ts_rank) never need normalizing.
    An empty/no-match lexical leg degrades gracefully to pure vector search. Scores returned to
    callers are RRF normalized to 0–1 (top hit = 1.0) since the UI displays them.
    """
    q = np.asarray(ai.embed([query])[0], dtype=np.float32)
    leg = max(40, k)  # candidates considered per leg before fusion
    rows = con.execute(
        """
        WITH vec AS (
            SELECT e.post_id AS id, ROW_NUMBER() OVER (ORDER BY e.vector <=> %s) AS r
            FROM embeddings e
            ORDER BY e.vector <=> %s
            LIMIT %s
        ), lex AS (
            SELECT p.id,
                   ROW_NUMBER() OVER (
                       ORDER BY ts_rank_cd(p.text_tsv, websearch_to_tsquery('english', %s)) DESC
                   ) AS r
            FROM posts p
            WHERE p.text_tsv @@ websearch_to_tsquery('english', %s)
            LIMIT %s
        ), fused AS (
            SELECT COALESCE(v.id, l.id) AS id,
                   COALESCE(1.0 / (60 + v.r), 0) + COALESCE(1.0 / (60 + l.r), 0) AS rrf
            FROM vec v FULL OUTER JOIN lex l ON l.id = v.id
        )
        SELECT p.id, p.url, p.text, a.handle, a.avatar_url, p.media_json, c.parent, f.rrf
        FROM fused f
        JOIN posts p ON p.id = f.id
        LEFT JOIN authors a ON a.tenant_id = p.tenant_id AND a.id = p.author_id
        LEFT JOIN LATERAL (
            SELECT category_id AS cid
            FROM assignments
            WHERE tenant_id = p.tenant_id AND post_id = p.id
            ORDER BY category_id
            LIMIT 1
        ) pa ON true
        LEFT JOIN categories c ON c.id = pa.cid
        ORDER BY f.rrf DESC, p.id
        LIMIT %s
        """,
        (q, q, leg, query, query, leg, k),
    ).fetchall()
    if not rows:
        return []
    top = float(rows[0][7])  # ORDER BY rrf DESC -> first row holds the max
    return [
        {"id": r[0], "url": r[1], "text": r[2], "handle": r[3], "avatar_url": r[4],
         "media_json": r[5], "parent": r[6], "score": float(r[7]) / top}
        for r in rows
    ]
