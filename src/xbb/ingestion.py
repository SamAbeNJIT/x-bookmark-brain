"""Ingestion seam: pull the user's bookmarks from X and parse them into records.

`XClient` is the single seam wrapping X's internal GraphQL bookmarks endpoint. Tests feed
recorded sample payloads (an original, a reply, a quote) to `parse_bookmark` and to
`run_backfill` via a fake client — no live X calls in tests (see docs/PRD.md → Testing
Decisions). `GraphQLXClient` is the live implementation; it needs real session credentials
and a current query id, so it is exercised manually, not in the test suite.

Scope note: this slice (#2) captures the bookmarked post itself plus a reference to its
immediate parent/quoted id. Resolving and storing the parent/quoted post bodies and the
author self-thread is the rich-context slice (#3).
"""

from __future__ import annotations

import json
from typing import Any, Iterator, Protocol

from .storage import connect, init_db


class XClient(Protocol):
    """Wraps authenticated access to X's internal bookmarks endpoint."""

    def iter_bookmark_pages(self) -> Iterator[list[dict[str, Any]]]:
        """Yield pages, each a list of raw per-tweet result payloads."""
        ...


def _unwrap(result: dict[str, Any]) -> dict[str, Any]:
    """Some results are wrapped as TweetWithVisibilityResults."""
    if result.get("__typename") == "TweetWithVisibilityResults" and "tweet" in result:
        return result["tweet"]
    return result


def parse_bookmark(raw: dict[str, Any]) -> dict[str, Any]:
    """Turn one raw X per-tweet payload into a parsed post record.

    Captures identity, content, author, media URLs + alt-text, hashtags, links, signals,
    and the immediate parent/quoted id. Retains the raw payload verbatim.
    """
    result = _unwrap(raw)
    rest_id = result.get("rest_id")
    legacy = result.get("legacy", {})

    user = result.get("core", {}).get("user_results", {}).get("result", {})
    user_legacy = user.get("legacy", {})
    author = {
        "id": user.get("rest_id"),
        "handle": user_legacy.get("screen_name"),
        "display_name": user_legacy.get("name"),
    }

    kind = "original"
    parent_post_id = None
    if legacy.get("in_reply_to_status_id_str"):
        kind = "reply"
        parent_post_id = legacy["in_reply_to_status_id_str"]
    quoted = result.get("quoted_status_result", {}).get("result")
    if quoted:
        kind = "quote"
        parent_post_id = _unwrap(quoted).get("rest_id")

    handle = author["handle"]
    url = f"https://x.com/{handle}/status/{rest_id}" if handle and rest_id else None

    entities = legacy.get("entities", {})
    hashtags = [h.get("text") for h in entities.get("hashtags", []) if h.get("text")]
    links = [u.get("expanded_url") for u in entities.get("urls", []) if u.get("expanded_url")]
    media = [
        {
            "url": m.get("media_url_https"),
            "alt_text": m.get("ext_alt_text"),
            "type": m.get("type"),
        }
        for m in legacy.get("extended_entities", {}).get("media", [])
    ]

    return {
        "id": rest_id,
        "url": url,
        "text": legacy.get("full_text"),
        "lang": legacy.get("lang"),
        "created_at": legacy.get("created_at"),
        "bookmarked_at": None,
        "author": author,
        "kind": kind,
        "parent_post_id": parent_post_id,
        "media": media,
        "hashtags": hashtags,
        "links": links,
        "like_count": legacy.get("favorite_count"),
        "repost_count": legacy.get("retweet_count"),
        "raw": raw,
    }


_POST_UPSERT = """
INSERT INTO posts (
    id, url, text, lang, created_at, bookmarked_at, author_id, kind, parent_post_id,
    media_json, hashtags_json, links_json, like_count, repost_count, raw_json
) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
ON CONFLICT(id) DO UPDATE SET
    url=excluded.url, text=excluded.text, lang=excluded.lang,
    created_at=excluded.created_at, bookmarked_at=excluded.bookmarked_at,
    author_id=excluded.author_id, kind=excluded.kind,
    parent_post_id=excluded.parent_post_id, media_json=excluded.media_json,
    hashtags_json=excluded.hashtags_json, links_json=excluded.links_json,
    like_count=excluded.like_count, repost_count=excluded.repost_count,
    raw_json=excluded.raw_json
"""

_AUTHOR_UPSERT = """
INSERT INTO authors (id, handle, display_name) VALUES (?,?,?)
ON CONFLICT(id) DO UPDATE SET handle=excluded.handle, display_name=excluded.display_name
"""


def store_bookmark(con, record: dict[str, Any]) -> None:
    """Upsert a parsed record's author and post. Idempotent by id."""
    author = record["author"]
    if author.get("id"):
        con.execute(_AUTHOR_UPSERT, (author["id"], author["handle"], author["display_name"]))
    con.execute(
        _POST_UPSERT,
        (
            record["id"],
            record["url"],
            record["text"],
            record["lang"],
            record["created_at"],
            record["bookmarked_at"],
            author.get("id"),
            record["kind"],
            record["parent_post_id"],
            json.dumps(record["media"]),
            json.dumps(record["hashtags"]),
            json.dumps(record["links"]),
            record["like_count"],
            record["repost_count"],
            json.dumps(record["raw"]),
        ),
    )


def run_backfill(client: XClient, db_path: str) -> int:
    """Page through all bookmarks, upsert by post id (idempotent), return count processed."""
    init_db(db_path)
    con = connect(db_path)
    count = 0
    try:
        for page in client.iter_bookmark_pages():
            for raw in page:
                record = parse_bookmark(raw)
                if record["id"] is None:
                    continue
                store_bookmark(con, record)
                count += 1
        con.commit()
    finally:
        con.close()
    return count


# Public web bearer token used by x.com's own client. Not a secret; ships in the web app.
_WEB_BEARER = (
    "Bearer AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D"
    "1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"
)


class GraphQLXClient:
    """Live client for X's internal bookmarks GraphQL endpoint.

    Requires the user's own session: `auth_token` + `ct0` cookies, and a current `query_id`
    (the hash in the Bookmarks request URL — copy it from your browser's network tab; it
    changes when X ships a new build). NOT covered by the test suite: it hits the network
    and depends on credentials. Verify locally.
    """

    def __init__(
        self,
        auth_token: str,
        csrf_token: str,
        query_id: str,
        features: dict[str, Any] | None = None,
        page_size: int = 100,
    ) -> None:
        self.auth_token = auth_token
        self.csrf_token = csrf_token
        self.query_id = query_id
        self.page_size = page_size
        # X requires a `features` blob that changes over time; supply the current one.
        self.features = features or {}

    def iter_bookmark_pages(self) -> Iterator[list[dict[str, Any]]]:  # pragma: no cover
        import httpx

        url = f"https://x.com/i/api/graphql/{self.query_id}/Bookmarks"
        headers = {
            "authorization": _WEB_BEARER,
            "x-csrf-token": self.csrf_token,
            "cookie": f"auth_token={self.auth_token}; ct0={self.csrf_token}",
            "content-type": "application/json",
        }
        cursor = None
        with httpx.Client(timeout=30) as http:
            while True:
                variables = {"count": self.page_size, "includePromotedContent": False}
                if cursor:
                    variables["cursor"] = cursor
                resp = http.get(
                    url,
                    headers=headers,
                    params={
                        "variables": json.dumps(variables),
                        "features": json.dumps(self.features),
                    },
                )
                resp.raise_for_status()
                entries = _timeline_entries(resp.json())
                tweets = [t for t in (_entry_tweet(e) for e in entries) if t is not None]
                next_cursor = _bottom_cursor(entries)
                if tweets:
                    yield tweets
                if not next_cursor or next_cursor == cursor or not tweets:
                    break
                cursor = next_cursor


def _timeline_entries(payload: dict[str, Any]) -> list[dict[str, Any]]:  # pragma: no cover
    timeline = (
        payload.get("data", {})
        .get("bookmark_timeline_v2", {})
        .get("timeline", {})
    )
    entries: list[dict[str, Any]] = []
    for instruction in timeline.get("instructions", []):
        entries.extend(instruction.get("entries", []))
    return entries


def _entry_tweet(entry: dict[str, Any]) -> dict[str, Any] | None:  # pragma: no cover
    if not entry.get("entryId", "").startswith("tweet-"):
        return None
    return (
        entry.get("content", {})
        .get("itemContent", {})
        .get("tweet_results", {})
        .get("result")
    )


def _bottom_cursor(entries: list[dict[str, Any]]) -> str | None:  # pragma: no cover
    for entry in entries:
        if entry.get("entryId", "").startswith("cursor-bottom-"):
            return entry.get("content", {}).get("value")
    return None
