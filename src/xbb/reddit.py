"""Reddit saved-post/comment adapter."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Iterator

from . import redditauth, sources

API = "https://oauth.reddit.com"
TOKEN_KEY = "reddit_oauth"
MAX_SAVED = 1000


def record_id(native_id: str) -> str:
    return sources.record_id("reddit", native_id)


def _timestamp(value: Any) -> str | None:
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return None


def parse_saved(child: dict[str, Any]) -> dict[str, Any] | None:
    data = child.get("data") if isinstance(child, dict) else None
    if not isinstance(data, dict) or not data.get("name") or child.get("kind") not in {"t1", "t3"}:
        return None
    is_comment = child["kind"] == "t1"
    permalink = data.get("permalink")
    reddit_url = f"https://www.reddit.com{permalink}" if permalink else None
    url = reddit_url if is_comment or data.get("is_self", True) else (data.get("url") or reddit_url)
    title = data.get("link_title") if is_comment else data.get("title")
    body = data.get("body") if is_comment else data.get("selftext")
    text = (body or "") if is_comment else f"{title or ''}\n{body or ''}"
    author_name = data.get("author")
    author = None if not author_name or author_name == "[deleted]" else {
        "id": f"reddit-user-{author_name}", "handle": author_name,
        "display_name": author_name, "avatar_url": None,
    }
    return {
        "id": record_id(data["name"]), "sort_index": None, "url": url, "text": text,
        "lang": None, "created_at": _timestamp(data.get("created_utc")),
        "bookmarked_at": None, "title": title, "author": author, "source": "reddit",
        "kind": "original",
        "parent_post_id": None, "parent": None, "media": [], "hashtags": [],
        "links": [{"url": url}] if url else [], "like_count": data.get("score"),
        "repost_count": None, "raw": child,
    }


class RedditApiClient:
    def __init__(self, con, client_id: str) -> None:
        self.con, self.client_id = con, client_id
        self._tok = sources.load_tokens(con, TOKEN_KEY)
        if not self._tok:
            raise RuntimeError("Not connected to Reddit — use the Connect Reddit flow first.")

    def _access(self) -> str:
        if time.time() >= self._tok.get("expires_at", 0) and self._tok.get("refresh_token"):
            fresh = redditauth.refresh_token(self.client_id, self._tok["refresh_token"])
            fresh.setdefault("refresh_token", self._tok["refresh_token"])
            fresh.setdefault("username", self._tok.get("username"))
            sources.save_tokens(self.con, TOKEN_KEY, fresh)
            self._tok = sources.load_tokens(self.con, TOKEN_KEY)
        return self._tok["access_token"]

    def _get(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        import httpx

        delay = 2.0
        for _ in range(6):
            response = httpx.get(
                f"{API}{path}", params=params,
                headers={"Authorization": f"Bearer {self._access()}",
                         "User-Agent": redditauth.USER_AGENT}, timeout=30.0,
            )
            if response.status_code == 429:
                wait = float(response.headers.get("x-ratelimit-reset", 0) or 0)
                time.sleep(min(max(wait, delay), 900.0))
                delay = min(delay * 2, 60.0)
                continue
            response.raise_for_status()
            return response.json()
        response.raise_for_status()
        return response.json()

    def iter_saved_pages(self, username: str) -> Iterator[list[dict[str, Any]]]:
        after, fetched = None, 0
        while fetched < MAX_SAVED:
            params = {"limit": str(min(100, MAX_SAVED - fetched)), "raw_json": "1"}
            if after:
                params["after"] = after
            page = self._get(f"/user/{username}/saved", params).get("data") or {}
            children = page.get("children") or []
            if not children:
                break
            remaining = MAX_SAVED - fetched
            children = children[:remaining]
            yield children
            fetched += len(children)
            after = page.get("after")
            if not after:
                break


def backfill(con, cfg, *, incremental: bool, max_total: int | None,
             client: RedditApiClient | None = None) -> int:
    client = client or RedditApiClient(con, cfg.reddit_client_id)
    tokens = sources.load_tokens(con, TOKEN_KEY) or {}
    username = tokens.get("username")
    if not username:
        raise RuntimeError("Reddit connection is missing the authorized username; reconnect Reddit.")
    return sources.backfill_pages(
        con, "reddit", client.iter_saved_pages(username), parse_saved,
        incremental=incremental, max_total=max_total,
    )


class RedditAdapter:
    name = "reddit"

    @staticmethod
    def is_configured(cfg) -> bool:
        return bool(cfg.reddit_client_id)

    @staticmethod
    def is_connected(con) -> bool:
        return sources.is_connected(con, TOKEN_KEY)

    @staticmethod
    def record_id(native_id: str) -> str:
        return record_id(native_id)

    @staticmethod
    def authorize_url(cfg, con, state: str) -> str:
        verifier, challenge = redditauth.make_pkce()
        from . import storage
        storage.set_pkce(con, state, verifier)
        return redditauth.authorize_url(cfg.reddit_client_id, cfg.reddit_redirect_uri,
                                        state, challenge)

    @staticmethod
    def handle_callback(cfg, con, code: str, state: str) -> None:
        from . import storage
        verifier = storage.pop_pkce(con, state)
        if not verifier or not code:
            raise ValueError("Reddit connection expired or invalid")
        tokens = redditauth.exchange_code(cfg.reddit_client_id, cfg.reddit_redirect_uri,
                                          code, verifier)
        me = redditauth.fetch_me(tokens["access_token"])
        tokens["username"] = me["name"]
        sources.save_tokens(con, TOKEN_KEY, tokens, cfg=cfg)

    @staticmethod
    def backfill(con, cfg, *, incremental: bool, max_total: int | None) -> int:
        return backfill(con, cfg, incremental=incremental, max_total=max_total)


ADAPTER = sources.register(RedditAdapter())
