"""Semantic search (issue #4): embed posts, store vectors, rank by cosine similarity.

Vectors are stored as JSON in the `embeddings` table and compared with a pure-Python
cosine — zero extra dependencies, fine for a personal corpus. Swapping in an ANN index
(sqlite-vec / LanceDB) later only touches this module.
"""

from __future__ import annotations

import json
import math
import sqlite3
from typing import Any

from .ai import AIClient


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def index_posts(con: sqlite3.Connection, ai: AIClient) -> int:
    """Embed posts that don't yet have an embedding. Returns how many were newly indexed."""
    rows = con.execute(
        """
        SELECT p.id, p.text
        FROM posts p
        LEFT JOIN embeddings e ON e.post_id = p.id
        WHERE e.post_id IS NULL AND p.text IS NOT NULL
        """
    ).fetchall()
    if not rows:
        return 0
    vectors = ai.embed([text for _, text in rows])
    for (post_id, _), vector in zip(rows, vectors):
        con.execute(
            "INSERT INTO embeddings (post_id, vector) VALUES (?, ?) "
            "ON CONFLICT(post_id) DO UPDATE SET vector = excluded.vector",
            (post_id, json.dumps(vector)),
        )
    con.commit()
    return len(rows)


def search(con: sqlite3.Connection, ai: AIClient, query: str, k: int = 10) -> list[dict[str, Any]]:
    """Return up to k posts ranked by semantic similarity to the query."""
    query_vector = ai.embed([query])[0]
    rows = con.execute(
        """
        SELECT p.id, p.url, p.text, a.handle, e.vector
        FROM embeddings e
        JOIN posts p ON p.id = e.post_id
        LEFT JOIN authors a ON a.id = p.author_id
        """
    ).fetchall()
    scored = [
        {
            "id": post_id,
            "url": url,
            "text": text,
            "handle": handle,
            "score": _cosine(query_vector, json.loads(vector)),
        }
        for post_id, url, text, handle, vector in rows
    ]
    scored.sort(key=lambda r: r["score"], reverse=True)
    return scored[:k]
