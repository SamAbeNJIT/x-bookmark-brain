"""Categorization (issues #5 + #6): taxonomy derivation/review and multi-label assignment.

Taxonomy is derived from the corpus by the AI seam, curated by the user (rename/merge/
delete), then used to multi-label posts. All AI calls go through the `AIClient` seam.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Callable

from .ai import AIClient


# --- #5: taxonomy derivation + review -------------------------------------------------

def derive_taxonomy(con: sqlite3.Connection, ai: AIClient, sample_size: int = 500) -> list[dict[str, str]]:
    """Ask the AI seam to propose a starter taxonomy from a sample of post texts.

    Samples randomly across the whole corpus (not insertion/recency order) so the proposed
    categories reflect the user's full history, not just what they bookmarked most recently.
    """
    texts = [r[0] for r in con.execute(
        "SELECT text FROM posts WHERE text IS NOT NULL ORDER BY RANDOM() LIMIT ?", (sample_size,)
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

def _labelable(text: str | None) -> bool:
    """Heuristic: does this post have enough real text to be worth a single-post retry?
    Filters out image-only / bare-link posts (which always come back empty)."""
    if not text:
        return False
    t = text.strip()
    return len(t) >= 40 and t.count(" ") >= 2


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
    batch_size: int = 20,
) -> int:
    """Label every post that has no assignment yet. Returns how many were processed.

    Labels in batches — many posts per AI call — so the taxonomy (most of the input) is sent
    once per batch instead of once per post (big cost/latency win at ~1k posts/sync). Resumable
    and interrupt-safe: only un-assigned posts are selected, and each batch commits before the
    next, so a re-run continues where an interrupted run left off.

    Self-heal: the batch path occasionally returns nothing for a post that does have real text;
    such posts get one single-post retry (which is more thorough). Posts with no meaningful text
    (image-only / bare links) are NOT retried — they'd just come back empty and waste a call.
    """
    rows = con.execute(
        """
        SELECT p.id, p.text FROM posts p
        LEFT JOIN assignments a ON a.post_id = p.id
        WHERE a.post_id IS NULL AND p.text IS NOT NULL
        """
    ).fetchall()
    total = len(rows)
    if not total:
        return 0
    taxonomy = get_taxonomy(con)
    name_to_id = {c["name"]: c["id"] for c in taxonomy}

    def _write(post_id: str, names: list[str]) -> None:
        for name in names:
            category_id = name_to_id.get(name)
            if category_id is not None:
                con.execute(
                    "INSERT OR IGNORE INTO assignments (post_id, category_id) VALUES (?, ?)",
                    (post_id, category_id),
                )

    processed = 0
    for start in range(0, total, batch_size):
        chunk = rows[start : start + batch_size]
        try:
            labels = ai.assign_categories_batch(
                [{"id": pid, "text": txt} for pid, txt in chunk], taxonomy
            )
        except Exception:
            labels = [[] for _ in chunk]
        for (post_id, text), names in zip(chunk, labels):
            if not names and _labelable(text):  # batch dropped a real post — retry once
                try:
                    names = ai.assign_categories(text, taxonomy)
                except Exception:
                    names = []
            _write(post_id, names)
            processed += 1
        con.commit()
        if progress is not None:
            progress(min(start + batch_size, total), total)
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


# Top-level groupings for the category tree. Maps a category name → its parent group.
# Categories not listed here (e.g. newly derived ones) fall under "Other" in the tree.
DEFAULT_PARENTS: dict[str, str] = {
    "AI Model Benchmarks & Comparisons": "AI & Engineering",
    "AI Coding Agents & Automation Loops": "AI & Engineering",
    "Open Source & Local AI Models": "AI & Engineering",
    "AI Developer Tools & Infrastructure": "AI & Engineering",
    "AI Industry News & Geopolitics": "AI & Engineering",
    "Productivity & Agentic Workflow Tips": "AI & Engineering",
    "Biotech & Medical Innovation": "Health & Longevity",
    "Peptides, Nootropics & Biohacking": "Health & Longevity",
    "Hormones, Lab Work & Metabolic Health": "Health & Longevity",
    "Nutrition, Diet & Lifestyle": "Health & Longevity",
    "Ethereum & Crypto Investing": "Finance & Crypto",
    "Stock Picks & Investment Theses": "Finance & Crypto",
    "Personal Finance & Wealth Psychology": "Finance & Crypto",
    "Geopolitics & American Power": "Politics & Society",
    "Politics & Social Controversy": "Politics & Society",
    "Social Dynamics, Dating & Male Psychology": "Politics & Society",
    "Religion, Christianity & Church Criticism": "Culture & Media",
    "Industrialization, Manufacturing & Hard Tech": "Science & Industry",
    "Science & Emerging Research": "Science & Industry",
    "Humor & Shitposting": "Culture & Media",
    "Quotes, History & Wisdom": "Culture & Media",
    "Book & Media Recommendations": "Culture & Media",
}


def apply_default_parents(con: sqlite3.Connection) -> int:
    """Set categories.parent from DEFAULT_PARENTS by name. Returns rows updated."""
    n = 0
    for name, parent in DEFAULT_PARENTS.items():
        cur = con.execute(
            "UPDATE categories SET parent = ? WHERE name = ?", (parent, name)
        )
        n += cur.rowcount
    con.commit()
    return n


def category_tree(con: sqlite3.Connection) -> list[dict[str, Any]]:
    """Group categories under their parent for the tree view.

    Returns [{parent, total, children: [{id, name, count}]}], parents sorted by total
    descending and children by count descending. Unparented categories group under "Other".
    """
    rows = con.execute(
        """
        SELECT c.id, c.name, c.parent, COUNT(a.post_id)
        FROM categories c
        LEFT JOIN assignments a ON a.category_id = c.id
        GROUP BY c.id, c.name, c.parent
        """
    ).fetchall()
    groups: dict[str, list[dict[str, Any]]] = {}
    for cid, name, parent, count in rows:
        groups.setdefault(parent or "Other", []).append(
            {"id": cid, "name": name, "count": count}
        )
    tree = []
    for parent, children in groups.items():
        children.sort(key=lambda c: -c["count"])
        tree.append(
            {"parent": parent, "total": sum(c["count"] for c in children), "children": children}
        )
    tree.sort(key=lambda g: -g["total"])
    return tree


def posts_in_category(con: sqlite3.Connection, category_id: int) -> list[dict[str, Any]]:
    row = con.execute("SELECT parent FROM categories WHERE id = ?", (category_id,)).fetchone()
    parent = row[0] if row else None
    return [
        {"id": r[0], "url": r[1], "text": r[2], "handle": r[3],
         "avatar_url": r[4], "media_json": r[5], "parent": parent}
        for r in con.execute(
            """
            SELECT p.id, p.url, p.text, au.handle, au.avatar_url, p.media_json
            FROM posts p
            JOIN assignments a ON a.post_id = p.id
            LEFT JOIN authors au ON au.id = p.author_id
            WHERE a.category_id = ?
            ORDER BY p.bm_rank DESC
            """,
            (category_id,),
        )
    ]


def unlabeled_count(con: sqlite3.Connection) -> int:
    """How many posts have no category assignment at all."""
    return con.execute(
        "SELECT COUNT(*) FROM posts p LEFT JOIN assignments a ON a.post_id = p.id "
        "WHERE a.post_id IS NULL"
    ).fetchone()[0]


def posts_unlabeled(con: sqlite3.Connection, limit: int = 600) -> list[dict[str, Any]]:
    """Posts with no category assignment, newest-saved first — the 'Unlabeled' bucket."""
    return [
        {"id": r[0], "url": r[1], "text": r[2], "handle": r[3],
         "avatar_url": r[4], "media_json": r[5], "parent": None}
        for r in con.execute(
            """
            SELECT p.id, p.url, p.text, au.handle, au.avatar_url, p.media_json
            FROM posts p
            LEFT JOIN assignments a ON a.post_id = p.id
            LEFT JOIN authors au ON au.id = p.author_id
            WHERE a.post_id IS NULL
            ORDER BY p.bm_rank DESC
            LIMIT ?
            """,
            (limit,),
        )
    ]


def feed_posts(
    con: sqlite3.Connection, parent: str | None = None, limit: int = 150, offset: int = 0
) -> list[dict[str, Any]]:
    """A page of posts for the color feed, each tagged with one parent group (for tinting).

    Filtered to a single parent group when given. A post in multiple groups is shown once,
    under one group (arbitrary when unfiltered; the matching one when filtered). `offset`
    drives the rolling/infinite-scroll paging.
    """
    cols = """
        SELECT p.id, p.url, p.text, au.handle, au.avatar_url, p.media_json, c.parent
        FROM posts p
        JOIN assignments a ON a.post_id = p.id
        JOIN categories c ON c.id = a.category_id
        LEFT JOIN authors au ON au.id = p.author_id
    """
    if parent:
        rows = con.execute(
            cols + " WHERE c.parent = ? GROUP BY p.id ORDER BY p.bm_rank DESC LIMIT ? OFFSET ?",
            (parent, limit, offset),
        )
    else:
        rows = con.execute(
            cols + " GROUP BY p.id ORDER BY p.bm_rank DESC LIMIT ? OFFSET ?", (limit, offset)
        )
    return [
        {"id": r[0], "url": r[1], "text": r[2], "handle": r[3],
         "avatar_url": r[4], "media_json": r[5], "parent": r[6]}
        for r in rows
    ]
