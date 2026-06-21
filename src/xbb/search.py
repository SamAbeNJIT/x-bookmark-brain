"""Semantic search (#4): embed posts (batched, resumable) and rank with numpy cosine.

Vectors are stored as float32 bytes in the `embeddings` table; search loads them into a
numpy matrix and ranks by cosine in a single matrix-vector product — exact and instant for
a personal corpus (tens to hundreds of thousands of vectors). Swapping to sqlite-vec/FAISS
later only touches this module.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Callable

import numpy as np

from .ai import AIClient


def _to_bytes(vector) -> bytes:
    return np.asarray(vector, dtype=np.float32).tobytes()


def _unindexed(con: sqlite3.Connection) -> list[tuple[str, str]]:
    return con.execute(
        """
        SELECT p.id, p.text
        FROM posts p
        LEFT JOIN embeddings e ON e.post_id = p.id
        WHERE e.post_id IS NULL AND p.text IS NOT NULL AND p.text <> ''
        """
    ).fetchall()


def index_posts(
    con: sqlite3.Connection,
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
        con.executemany(
            "INSERT INTO embeddings (post_id, vector) VALUES (?, ?) "
            "ON CONFLICT(post_id) DO UPDATE SET vector = excluded.vector",
            [(post_id, _to_bytes(vec)) for (post_id, _), vec in zip(chunk, vectors)],
        )
        con.commit()
        done += len(chunk)
        if progress is not None:
            progress(done, total)
    return done


def _load_matrix(con: sqlite3.Connection):
    rows = con.execute(
        """
        SELECT p.id, p.url, p.text, a.handle, e.vector, a.avatar_url, p.media_json
        FROM embeddings e
        JOIN posts p ON p.id = e.post_id
        LEFT JOIN authors a ON a.id = p.author_id
        """
    ).fetchall()
    if not rows:
        return [], None
    meta = [
        {"id": r[0], "url": r[1], "text": r[2], "handle": r[3],
         "avatar_url": r[5], "media_json": r[6]}
        for r in rows
    ]
    matrix = np.stack([np.frombuffer(r[4], dtype=np.float32) for r in rows])
    return meta, matrix


def search(con: sqlite3.Connection, ai: AIClient, query: str, k: int = 10) -> list[dict[str, Any]]:
    """Return up to k posts ranked by cosine similarity to the query (exact, numpy)."""
    meta, matrix = _load_matrix(con)
    if not meta:
        return []
    q = np.asarray(ai.embed([query])[0], dtype=np.float32)
    denom = np.linalg.norm(matrix, axis=1) * (float(np.linalg.norm(q)) or 1e-9)
    denom[denom == 0] = 1e-9
    scores = (matrix @ q) / denom
    k = min(k, len(meta))
    top = np.argsort(-scores)[:k]
    return [{**meta[i], "score": float(scores[i])} for i in top]
