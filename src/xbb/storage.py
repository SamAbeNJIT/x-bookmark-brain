"""Local persistence (SQLite) for bookmarks, authors, taxonomy, and assignments.

This is a scaffold: the schema below encodes the data decisions from docs/PRD.md.
Implementation (migrations, queries, vector index wiring) lands in the foundation slice.
"""

from __future__ import annotations

# Schema sketch — the decision record, not the final DDL.
#
# authors(id, handle, display_name)
# posts(
#   id, url, text, lang, created_at, bookmarked_at,
#   author_id -> authors.id,
#   kind,                  -- 'original' | 'reply' | 'quote'
#   parent_post_id,        -- parent (reply) or quoted (quote) post, nullable
#   media_json,            -- [{url, alt_text, type}]
#   hashtags_json, links_json, like_count, repost_count,
#   raw_json               -- original X payload, retained verbatim
# )
# self_thread_posts(root_post_id -> posts.id, position, post_id -> posts.id)
# categories(id, name, definition)
# assignments(post_id -> posts.id, category_id -> categories.id)  -- multi-label
# embeddings(post_id -> posts.id, vector)                         -- vector index
#
# Notes:
#  - Upsert posts by id so backfill is idempotent.
#  - raw_json is the safety net against under-modeling.


def init_db(db_path: str) -> None:
    """Create tables and the vector index if they don't exist. (TODO: implement.)"""
    raise NotImplementedError("foundation slice")
