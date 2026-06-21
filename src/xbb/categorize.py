"""Categorization (issues #5 + #6): taxonomy derivation/review and multi-label assignment.

Taxonomy is derived from the corpus by the AI seam, curated by the user (rename/merge/
delete), then used to multi-label posts. All AI calls go through the `AIClient` seam.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Callable

from .ai import AIClient


# --- #5: taxonomy derivation + review -------------------------------------------------

def derive_taxonomy(con: sqlite3.Connection, ai: AIClient, sample_size: int = 200) -> list[dict[str, str]]:
    """Ask the AI seam to propose a starter taxonomy from a sample of post texts."""
    texts = [r[0] for r in con.execute(
        "SELECT text FROM posts WHERE text IS NOT NULL LIMIT ?", (sample_size,)
    )]
    return ai.derive_taxonomy(texts)


def save_taxonomy(con: sqlite3.Connection, categories: list[dict[str, str]]) -> None:
    """Persist the user-approved categories (upsert by name)."""
    for c in categories:
        con.execute(
            "INSERT INTO categories (name, definition) VALUES (?, ?) "
            "ON CONFLICT(name) DO UPDATE SET definition = excluded.definition",
            (c["name"], c.get("definition")),
        )
    con.commit()


def get_taxonomy(con: sqlite3.Connection) -> list[dict[str, Any]]:
    return [
        {"id": r[0], "name": r[1], "definition": r[2]}
        for r in con.execute("SELECT id, name, definition FROM categories ORDER BY name")
    ]


def rename_category(con: sqlite3.Connection, category_id: int, new_name: str) -> None:
    con.execute("UPDATE categories SET name = ? WHERE id = ?", (new_name, category_id))
    con.commit()


def delete_category(con: sqlite3.Connection, category_id: int) -> None:
    con.execute("DELETE FROM assignments WHERE category_id = ?", (category_id,))
    con.execute("DELETE FROM categories WHERE id = ?", (category_id,))
    con.commit()


def merge_categories(con: sqlite3.Connection, source_id: int, target_id: int) -> None:
    """Move source's assignments to target, then drop source."""
    con.execute(
        "UPDATE OR IGNORE assignments SET category_id = ? WHERE category_id = ?",
        (target_id, source_id),
    )
    con.execute("DELETE FROM assignments WHERE category_id = ?", (source_id,))
    con.execute("DELETE FROM categories WHERE id = ?", (source_id,))
    con.commit()


# --- #6: multi-label assignment + browse ----------------------------------------------

def assign_post(con: sqlite3.Connection, ai: AIClient, post_id: str) -> list[str]:
    """Assign one post to one or more existing categories. Returns the applied names."""
    row = con.execute("SELECT text FROM posts WHERE id = ?", (post_id,)).fetchone()
    if not row or row[0] is None:
        return []
    taxonomy = get_taxonomy(con)
    name_to_id = {c["name"]: c["id"] for c in taxonomy}
    applied = []
    for name in ai.assign_categories(row[0], taxonomy):
        category_id = name_to_id.get(name)
        if category_id is not None:
            con.execute(
                "INSERT OR IGNORE INTO assignments (post_id, category_id) VALUES (?, ?)",
                (post_id, category_id),
            )
            applied.append(name)
    con.commit()
    return applied


def assign_unassigned(
    con: sqlite3.Connection,
    ai: AIClient,
    progress: Callable[[int, int], None] | None = None,
) -> int:
    """Label every post that has no assignment yet. Returns how many were processed.

    Resumable: only posts without an assignment are processed, and each is committed as it
    goes (via assign_post), so re-running continues where an interrupted run left off.
    """
    rows = con.execute(
        """
        SELECT p.id FROM posts p
        LEFT JOIN assignments a ON a.post_id = p.id
        WHERE a.post_id IS NULL AND p.text IS NOT NULL
        """
    ).fetchall()
    total = len(rows)
    processed = 0
    for i, (post_id,) in enumerate(rows, 1):
        try:
            assign_post(con, ai, post_id)
            processed += 1
        except Exception:
            pass  # never let one bad post abort the batch; a re-run retries it
        if progress is not None:
            progress(i, total)
    return processed


def categories_with_counts(con: sqlite3.Connection) -> list[dict[str, Any]]:
    return [
        {"id": r[0], "name": r[1], "count": r[2]}
        for r in con.execute(
            """
            SELECT c.id, c.name, COUNT(a.post_id)
            FROM categories c
            LEFT JOIN assignments a ON a.category_id = c.id
            GROUP BY c.id, c.name
            ORDER BY c.name
            """
        )
    ]


def posts_in_category(con: sqlite3.Connection, category_id: int) -> list[dict[str, Any]]:
    return [
        {"id": r[0], "url": r[1], "text": r[2], "handle": r[3]}
        for r in con.execute(
            """
            SELECT p.id, p.url, p.text, au.handle
            FROM posts p
            JOIN assignments a ON a.post_id = p.id
            LEFT JOIN authors au ON au.id = p.author_id
            WHERE a.category_id = ?
            ORDER BY p.bookmarked_at
            """,
            (category_id,),
        )
    ]
