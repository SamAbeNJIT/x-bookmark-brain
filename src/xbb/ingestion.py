"""Ingestion helpers: parse raw bookmark payloads into records and upsert them.

Live X ingestion now goes through the sanctioned OAuth v2 API in `xapi.py`. This module keeps
the pure, network-free pieces it reuses: `parse_bookmark` (legacy internal-GraphQL shape,
exercised by the test suite), `_upsert_post`/`_upsert_author` (the generic record → DB upsert,
reused by `xapi`), and `run_backfill` (generic page-and-upsert loop with resume, used by tests).
"""

from __future__ import annotations

import json
import time
from typing import Any, Iterator, Protocol

from . import storage


class XClient(Protocol):
    """Wraps authenticated access to X's internal bookmarks endpoint."""

    def iter_bookmark_pages(self) -> Iterator[list[dict[str, Any]]]:
        """Yield pages of raw bookmark entries, newest to oldest (X's natural order)."""
        ...


# --------------------------------------------------------------------------- parsing


def _unwrap_tweet(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Dig the actual Tweet object out of an entry / tweet_results / visibility wrapper."""
    node: Any = raw
    if isinstance(node, dict) and "content" in node:  # a timeline entry
        node = (
            node.get("content", {})
            .get("itemContent", {})
            .get("tweet_results", {})
            .get("result")
        )
    elif isinstance(node, dict) and "tweet_results" in node:
        node = node.get("tweet_results", {}).get("result")
    elif isinstance(node, dict) and "result" in node and "legacy" not in node:
        node = node.get("result")  # quoted_status_result wrapper
    if not isinstance(node, dict):
        return None
    if node.get("__typename") == "TweetWithVisibilityResults":
        node = node.get("tweet")
    if not isinstance(node, dict):
        return None
    return node


def _author(tweet: dict[str, Any]) -> dict[str, Any]:
    user = tweet.get("core", {}).get("user_results", {}).get("result", {}) or {}
    legacy = user.get("legacy", {}) or {}
    core = user.get("core", {}) or {}  # newer payloads put handle/name here
    avatar = (user.get("avatar") or {}).get("image_url") or legacy.get(
        "profile_image_url_https"
    )
    return {
        "id": user.get("rest_id"),
        "handle": core.get("screen_name") or legacy.get("screen_name"),
        "display_name": core.get("name") or legacy.get("name"),
        "avatar_url": avatar,
    }


def _full_text(tweet: dict[str, Any], legacy: dict[str, Any]) -> str:
    note = (
        tweet.get("note_tweet", {})
        .get("note_tweet_results", {})
        .get("result", {})
    )
    if note.get("text"):  # longform note tweet carries the untruncated text
        return note["text"]
    return legacy.get("full_text", "") or ""


def _media(legacy: dict[str, Any]) -> list[dict[str, Any]]:
    ext = legacy.get("extended_entities", {}) or legacy.get("entities", {})
    out = []
    for m in ext.get("media", []) or []:
        out.append(
            {
                "url": m.get("media_url_https"),
                "type": m.get("type"),
                "alt_text": m.get("ext_alt_text"),
            }
        )
    return out


def parse_bookmark(raw: dict[str, Any]) -> dict[str, Any]:
    """Turn one raw X payload (timeline entry or tweet result) into a parsed post record.

    Captures identity, content, author, media + alt-text, signals, and context: the quoted
    post (inline, fully parsed) for quotes, and the parent post id for replies. Retains the
    raw payload verbatim under "raw".
    """
    tweet = _unwrap_tweet(raw)
    if tweet is None or "legacy" not in tweet:
        raise ValueError("payload is not a parseable bookmarked tweet")
    legacy = tweet.get("legacy", {}) or {}
    author = _author(tweet)
    post_id = tweet.get("rest_id") or legacy.get("id_str")

    quoted_raw = tweet.get("quoted_status_result")
    parent: dict[str, Any] | None = None
    if legacy.get("in_reply_to_status_id_str"):
        kind = "reply"
        parent_post_id = legacy["in_reply_to_status_id_str"]
        # The bookmarks endpoint gives only the parent id, not its text; full parent capture
        # would need a TweetDetail fetch (follow-up). Record the id so context is not lost.
        parent = {"id": parent_post_id, "text": None}
    elif quoted_raw or legacy.get("is_quote_status"):
        kind = "quote"
        try:
            parent = parse_bookmark(quoted_raw) if quoted_raw else None
        except ValueError:
            parent = None  # quoted payload is a stub (deleted/withheld); keep the id only
        parent_post_id = (
            (parent["id"] if parent else None)
            or (quoted_raw or {}).get("result", {}).get("rest_id")
            or legacy.get("quoted_status_id_str")
        )
    else:
        kind = "original"
        parent_post_id = None

    handle = author.get("handle")
    url = f"https://x.com/{handle}/status/{post_id}" if handle and post_id else None
    entities = legacy.get("entities", {}) or {}
    # X's timeline sortIndex is the authoritative bookmark-order key (higher = saved later).
    try:
        sort_index = int(raw["sortIndex"]) if isinstance(raw, dict) and raw.get("sortIndex") else None
    except (ValueError, TypeError):
        sort_index = None
    return {
        "id": post_id,
        "sort_index": sort_index,
        "url": url,
        "text": _full_text(tweet, legacy),
        "lang": legacy.get("lang"),
        "created_at": legacy.get("created_at"),
        "bookmarked_at": None,  # not exposed per-entry by the bookmarks endpoint
        "author": author,
        "kind": kind,
        "parent_post_id": parent_post_id,
        "parent": parent,
        "media": _media(legacy),
        "hashtags": [h.get("text") for h in entities.get("hashtags", []) if h.get("text")],
        "links": [u.get("expanded_url") for u in entities.get("urls", []) if u.get("expanded_url")],
        "like_count": legacy.get("favorite_count"),
        "repost_count": legacy.get("retweet_count"),
        "raw": raw,
    }


# --------------------------------------------------------------------------- backfill


def _upsert_author(con: Any, author: dict[str, Any]) -> None:
    if not author.get("id"):
        return
    con.execute(
        "INSERT INTO authors (id, handle, display_name, avatar_url) VALUES (%s, %s, %s, %s) "
        "ON CONFLICT (tenant_id, id) DO UPDATE SET handle=excluded.handle, "
        "display_name=excluded.display_name, avatar_url=excluded.avatar_url",
        (author["id"], author.get("handle"), author.get("display_name"), author.get("avatar_url")),
    )


def _upsert_post(con: Any, rec: dict[str, Any]) -> None:
    if not rec.get("id"):
        return
    _upsert_author(con, rec.get("author", {}) or {})
    con.execute(
        """
        INSERT INTO posts (
            id, source, url, text, lang, created_at, bookmarked_at, author_id, kind,
            parent_post_id, media_json, hashtags_json, links_json, like_count,
            repost_count, raw_json, bm_rank
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (tenant_id, id) DO UPDATE SET
            url=excluded.url, text=excluded.text, lang=excluded.lang,
            created_at=excluded.created_at, author_id=excluded.author_id, kind=excluded.kind,
            parent_post_id=excluded.parent_post_id, media_json=excluded.media_json,
            hashtags_json=excluded.hashtags_json, links_json=excluded.links_json,
            like_count=excluded.like_count, repost_count=excluded.repost_count,
            raw_json=excluded.raw_json,
            bm_rank=COALESCE(excluded.bm_rank, posts.bm_rank)
        """,
        (
            rec["id"],
            rec.get("source", "x"),
            rec.get("url"),
            rec.get("text"),
            rec.get("lang"),
            rec.get("created_at"),
            rec.get("bookmarked_at"),
            (rec.get("author") or {}).get("id"),
            rec.get("kind"),
            rec.get("parent_post_id"),
            json.dumps(rec.get("media", [])),
            json.dumps(rec.get("hashtags", [])),
            json.dumps(rec.get("links", [])),
            rec.get("like_count"),
            rec.get("repost_count"),
            json.dumps(rec.get("raw")),
            rec.get("sort_index"),
        ),
    )


def run_backfill(
    client: XClient, dsn: str, tenant_id: str | None = None,
    incremental: bool = False, resume: bool = False
) -> int:
    """Page through bookmarks, upsert by post id (idempotent), return count stored.

    Stores only the bookmarked posts themselves; the immediate parent (reply) and quoted
    (quote) posts are retained as ids on each record. Resolving and persisting those
    parent/quoted bodies and author self-threads is the rich-context slice (#3).

    With `incremental=True`, stop once a whole page contains only posts already in the DB.
    Since X returns bookmarks newest-first, that means we've reached previously-synced posts
    — so a top-up fetches just the new slice instead of re-paging the entire timeline.

    With `resume=True`, start paging from the saved cursor (from a previous run that X
    rate-limited) instead of the top. For a full backfill (not incremental), the live
    client's pagination cursor is persisted after every page, so a run that stops partway
    can be continued with `resume=True` rather than re-paging from the start. Cleared when
    the timeline is fully paged.
    """
    storage.init_db(dsn, tenant_id)
    con = storage.connect(dsn, tenant_id)
    # Cursor checkpointing only applies to a full backfill on the live client. (Incremental
    # tops up from the top and would otherwise overwrite the gap-fill resume point.)
    tracks_cursor = hasattr(client, "cursor") and not incremental
    if resume and hasattr(client, "start_cursor"):
        client.start_cursor = storage.get_sync_cursor(con)  # None → start from the top
    count = 0
    try:
        for page in client.iter_bookmark_pages():
            new_in_page = 0
            for raw in page:
                try:
                    rec = parse_bookmark(raw)
                except ValueError:
                    continue  # skip non-tweet entries defensively
                exists = con.execute("SELECT 1 FROM posts WHERE id = %s", (rec["id"],)).fetchone()
                _upsert_post(con, rec)  # stores bm_rank = X's sortIndex (true bookmark order)
                count += 1
                if not exists:
                    new_in_page += 1
            con.commit()
            if tracks_cursor:
                storage.set_sync_cursor(con, getattr(client, "cursor"))  # kill-safe checkpoint
            if incremental and new_in_page == 0:
                break  # caught up to already-synced bookmarks
    finally:
        if tracks_cursor:
            storage.set_sync_cursor(con, getattr(client, "cursor", None))
        con.close()
    return count
