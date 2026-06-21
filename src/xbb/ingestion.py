"""Ingestion seam: pull the user's bookmarks from X and parse them into records.

`XClient` is the single seam wrapping X's internal GraphQL bookmarks endpoint. Tests feed
recorded sample payloads (a reply, a quote, a self-thread) to `parse_bookmark` and assert
the records produced — no live X calls in tests (see docs/PRD.md -> Testing Decisions).

`GraphQLXClient` is the live implementation of that seam: it authenticates with the user's
own session cookies (auth_token + ct0) and pages through X's internal Bookmarks endpoint.
"""

from __future__ import annotations

import json
import time
from typing import Any, Iterator, Protocol

from . import storage

# X web app's public bearer token (same value the browser uses for unauthed-app GraphQL).
WEB_BEARER = (
    "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D"
    "1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"
)

# Default GraphQL feature flags captured from a logged-in web session. X rotates these; if a
# request 400s complaining about a missing feature, recapture from DevTools and pass `features`.
DEFAULT_FEATURES: dict[str, bool] = {
    "rweb_video_screen_enabled": False,
    "rweb_cashtags_enabled": True,
    "profile_label_improvements_pcf_label_in_post_enabled": True,
    "responsive_web_profile_redirect_enabled": False,
    "rweb_tipjar_consumption_enabled": False,
    "verified_phone_label_enabled": False,
    "creator_subscriptions_tweet_preview_api_enabled": True,
    "responsive_web_graphql_timeline_navigation_enabled": True,
    "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
    "premium_content_api_read_enabled": False,
    "communities_web_enable_tweet_community_results_fetch": True,
    "c9s_tweet_anatomy_moderator_badge_enabled": True,
    "responsive_web_grok_analyze_button_fetch_trends_enabled": False,
    "responsive_web_grok_analyze_post_followups_enabled": True,
    "rweb_cashtags_composer_attachment_enabled": True,
    "responsive_web_jetfuel_frame": True,
    "responsive_web_grok_share_attachment_enabled": True,
    "responsive_web_grok_annotations_enabled": True,
    "articles_preview_enabled": True,
    "responsive_web_edit_tweet_api_enabled": True,
    "rweb_conversational_replies_downvote_enabled": False,
    "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
    "view_counts_everywhere_api_enabled": True,
    "longform_notetweets_consumption_enabled": True,
    "responsive_web_twitter_article_tweet_consumption_enabled": True,
    "content_disclosure_indicator_enabled": True,
    "content_disclosure_ai_generated_indicator_enabled": True,
    "responsive_web_grok_show_grok_translated_post": True,
    "responsive_web_grok_analysis_button_from_backend": True,
    "post_ctas_fetch_enabled": False,
    "freedom_of_speech_not_reach_fetch_enabled": True,
    "standardized_nudges_misinfo": True,
    "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled": True,
    "longform_notetweets_rich_text_read_enabled": True,
    "longform_notetweets_inline_media_enabled": False,
    "responsive_web_grok_image_annotation_enabled": True,
    "responsive_web_grok_imagine_annotation_enabled": True,
    "responsive_web_grok_community_note_auto_translation_is_enabled": True,
    "responsive_web_enhance_cards_enabled": False,
}

DEFAULT_QUERY_ID = "i8QZ1qqy36ffA3bxfTaf7w"


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
    return {
        "id": user.get("rest_id"),
        "handle": core.get("screen_name") or legacy.get("screen_name"),
        "display_name": core.get("name") or legacy.get("name"),
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
    return {
        "id": post_id,
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


# --------------------------------------------------------------------------- live client


class GraphQLXClient:
    """Live `XClient` over X's internal GraphQL Bookmarks endpoint, authenticated by cookies."""

    def __init__(
        self,
        auth_token: str,
        csrf_token: str,
        query_id: str = DEFAULT_QUERY_ID,
        features: dict[str, bool] | None = None,
        count: int = 20,
        page_pause_s: float = 0.7,
    ) -> None:
        if not auth_token or not csrf_token:
            raise ValueError("auth_token and csrf_token (ct0) are required")
        self.auth_token = auth_token
        self.csrf_token = csrf_token
        self.query_id = query_id
        self.features = features or DEFAULT_FEATURES
        self.count = count
        self.page_pause_s = page_pause_s

    @property
    def _url(self) -> str:
        return f"https://x.com/i/api/graphql/{self.query_id}/Bookmarks"

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "authorization": f"Bearer {WEB_BEARER}",
            "x-csrf-token": self.csrf_token,
            "x-twitter-active-user": "yes",
            "x-twitter-auth-type": "OAuth2Session",
            "x-twitter-client-language": "en",
            "content-type": "application/json",
            "accept": "*/*",
            "cookie": f"auth_token={self.auth_token}; ct0={self.csrf_token}",
            "user-agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            ),
            "referer": "https://x.com/i/bookmarks",
            "x-twitter-client-version": "web",
        }

    def _fetch(self, cursor: str | None) -> dict[str, Any]:
        import httpx

        variables: dict[str, Any] = {"count": self.count, "includePromotedContent": False}
        if cursor:
            variables["cursor"] = cursor
        params = {
            "variables": json.dumps(variables, separators=(",", ":")),
            "features": json.dumps(self.features, separators=(",", ":")),
        }
        delay = 30.0
        for _ in range(6):
            resp = httpx.get(self._url, headers=self._headers, params=params, timeout=30.0)
            if resp.status_code == 429:
                # X bookmarks rate limit (resets on a ~15-min window). Honor Retry-After,
                # else back off and retry rather than aborting the whole backfill.
                wait = float(resp.headers.get("retry-after", 0)) or delay
                time.sleep(min(wait, 300.0))
                delay = min(delay * 2, 300.0)
                continue
            resp.raise_for_status()
            return resp.json()
        resp.raise_for_status()  # exhausted retries
        return resp.json()

    @staticmethod
    def _split(page: dict[str, Any]) -> tuple[list[dict[str, Any]], str | None]:
        """Return (tweet entries, next bottom cursor) from a raw GraphQL page."""
        timeline = (
            page.get("data", {})
            .get("bookmark_timeline_v2", {})
            .get("timeline", {})
        )
        tweets: list[dict[str, Any]] = []
        cursor: str | None = None
        for inst in timeline.get("instructions", []):
            if inst.get("type") != "TimelineAddEntries":
                continue
            for entry in inst.get("entries", []):
                content = entry.get("content", {})
                etype = content.get("entryType")
                if etype == "TimelineTimelineItem":
                    if content.get("itemContent", {}).get("itemType") == "TimelineTweet":
                        tweets.append(entry)
                elif etype == "TimelineTimelineCursor" and content.get("cursorType") == "Bottom":
                    cursor = content.get("value")
        return tweets, cursor

    def iter_bookmark_pages(self) -> Iterator[list[dict[str, Any]]]:
        cursor: str | None = None
        seen_cursors: set[str] = set()
        empties = 0
        while True:
            page = self._fetch(cursor)
            tweets, next_cursor = self._split(page)
            if tweets:
                empties = 0
                yield tweets
                if not next_cursor or next_cursor in seen_cursors:
                    break  # no advance = real end of the timeline
                seen_cursors.add(next_cursor)
                cursor = next_cursor
                time.sleep(self.page_pause_s)
            else:
                # An empty page mid-stream is usually a transient rate-limit, not the
                # end. Back off and retry the SAME cursor; only stop after several in a
                # row (sustained empties = genuine end or a hard block).
                empties += 1
                if empties >= 4:
                    break
                time.sleep(self.page_pause_s * 5)


# --------------------------------------------------------------------------- backfill


def _upsert_author(con: Any, author: dict[str, Any]) -> None:
    if not author.get("id"):
        return
    con.execute(
        "INSERT INTO authors (id, handle, display_name) VALUES (?, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET handle=excluded.handle, display_name=excluded.display_name",
        (author["id"], author.get("handle"), author.get("display_name")),
    )


def _upsert_post(con: Any, rec: dict[str, Any]) -> None:
    if not rec.get("id"):
        return
    _upsert_author(con, rec.get("author", {}) or {})
    con.execute(
        """
        INSERT INTO posts (
            id, url, text, lang, created_at, bookmarked_at, author_id, kind,
            parent_post_id, media_json, hashtags_json, links_json, like_count,
            repost_count, raw_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            url=excluded.url, text=excluded.text, lang=excluded.lang,
            created_at=excluded.created_at, author_id=excluded.author_id, kind=excluded.kind,
            parent_post_id=excluded.parent_post_id, media_json=excluded.media_json,
            hashtags_json=excluded.hashtags_json, links_json=excluded.links_json,
            like_count=excluded.like_count, repost_count=excluded.repost_count,
            raw_json=excluded.raw_json
        """,
        (
            rec["id"],
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
        ),
    )


def run_backfill(client: XClient, db_path: str) -> int:
    """Page through all bookmarks, upsert by post id (idempotent), return count stored.

    Stores only the bookmarked posts themselves; the immediate parent (reply) and quoted
    (quote) posts are retained as ids on each record. Resolving and persisting those
    parent/quoted bodies and author self-threads is the rich-context slice (#3).
    """
    storage.init_db(db_path)
    con = storage.connect(db_path)
    count = 0
    try:
        for page in client.iter_bookmark_pages():
            for raw in page:
                try:
                    rec = parse_bookmark(raw)
                except ValueError:
                    continue  # skip non-tweet entries defensively
                _upsert_post(con, rec)
                count += 1
            con.commit()
    finally:
        con.close()
    return count
