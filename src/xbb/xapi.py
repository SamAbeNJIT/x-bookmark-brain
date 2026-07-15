"""X API v2 ingestion: pull the user's bookmarks via GET /2/users/{id}/bookmarks using the
OAuth token from xauth. Replaces the (TOS-violating) internal-GraphQL cookie client.

`parse_bookmark_v2` turns one v2 tweet (+ the page's includes) into the same generic record
dict the rest of the app already consumes — so embed/search/categorize/render are untouched.
"""

from __future__ import annotations

import json
import time
from typing import Any, Iterator

from . import crypto, storage, xauth
from .config import Config
from .ingestion import _upsert_post

API = "https://api.twitter.com/2"
_TOKEN_KEY = "x_oauth"  # sync_state key holding {access_token, refresh_token, expires_at}


# --------------------------------------------------------------------------- token store

def _enc_ctx(con) -> dict[str, str]:
    """Bind the token ciphertext to the current tenant via the KMS encryption context."""
    row = con.execute("SELECT current_setting('app.current_tenant', true)").fetchone()
    return {"tenant_id": row[0]} if row and row[0] else {}


def save_tokens(con, tok: dict[str, Any]) -> None:
    """Persist an OAuth token response (KMS-encrypted if configured), stamping an absolute expiry."""
    cfg = Config.from_env()
    rec = {
        "access_token": tok["access_token"],
        "refresh_token": tok.get("refresh_token"),
        "expires_at": time.time() + int(tok.get("expires_in", 7200)) - 60,
    }
    blob = crypto.encrypt(json.dumps(rec), cfg.kms_key_id, cfg.aws_region, _enc_ctx(con))
    storage.set_state(con, _TOKEN_KEY, blob)


def load_tokens(con) -> dict[str, Any] | None:
    raw = storage.get_state(con, _TOKEN_KEY)
    if not raw:
        return None
    cfg = Config.from_env()
    return json.loads(crypto.decrypt(raw, cfg.aws_region, _enc_ctx(con)))


def is_connected(con) -> bool:
    return load_tokens(con) is not None


# --------------------------------------------------------------------------- parsing

def parse_bookmark_v2(
    tweet: dict[str, Any], users: dict[str, Any], media: dict[str, Any]
) -> dict[str, Any]:
    """One v2 tweet + the page's includes maps → the generic record dict (see ingestion)."""
    tid = tweet.get("id")
    author_id = tweet.get("author_id")
    u = users.get(author_id, {}) if author_id else {}
    handle = u.get("username")
    text = (tweet.get("note_tweet") or {}).get("text") or tweet.get("text") or ""

    kind, parent_post_id = "original", None
    for ref in tweet.get("referenced_tweets", []) or []:
        if ref.get("type") == "replied_to":
            kind, parent_post_id = "reply", ref.get("id")
        elif ref.get("type") == "quoted":
            kind, parent_post_id = "quote", ref.get("id")

    media_out = []
    for key in (tweet.get("attachments") or {}).get("media_keys", []) or []:
        m = media.get(key)
        if m:
            media_out.append(
                {"url": m.get("url") or m.get("preview_image_url"),
                 "type": m.get("type"), "alt_text": m.get("alt_text")}
            )
    entities = tweet.get("entities") or {}
    metrics = tweet.get("public_metrics") or {}
    return {
        "id": tid,
        "sort_index": None,  # v2 has no sortIndex; bm_rank assigned by fetch order in backfill
        "url": f"https://x.com/{handle}/status/{tid}" if handle and tid else None,
        "text": text,
        "lang": tweet.get("lang"),
        "created_at": tweet.get("created_at"),
        "bookmarked_at": None,
        "author": {
            "id": author_id,
            "handle": handle,
            "display_name": u.get("name"),
            "avatar_url": u.get("profile_image_url"),
        },
        "kind": kind,
        "parent_post_id": parent_post_id,
        "parent": None,
        "media": media_out,
        "hashtags": [h.get("tag") for h in entities.get("hashtags", []) if h.get("tag")],
        "links": [u2.get("expanded_url") for u2 in entities.get("urls", []) if u2.get("expanded_url")],
        "like_count": metrics.get("like_count"),
        "repost_count": metrics.get("retweet_count"),
        "raw": tweet,
    }


# --------------------------------------------------------------------------- live client

_BOOKMARK_PARAMS = {
    "max_results": "100",
    "expansions": "author_id,attachments.media_keys,referenced_tweets.id",
    "tweet.fields": "created_at,lang,public_metrics,entities,note_tweet,referenced_tweets,attachments",
    "user.fields": "username,name,profile_image_url",
    "media.fields": "type,url,preview_image_url,alt_text",
}


class XApiClient:
    """Authenticated v2 client; refreshes the access token on demand."""

    def __init__(self, con, client_id: str) -> None:
        self.con = con
        self.client_id = client_id
        self._tok = load_tokens(con)
        if not self._tok:
            raise RuntimeError("Not connected to X — run the OAuth connect flow first.")

    def _access(self) -> str:
        if time.time() >= self._tok["expires_at"] and self._tok.get("refresh_token"):
            fresh = xauth.refresh_token(self.client_id, self._tok["refresh_token"])
            # X may not return a new refresh_token; keep the old one if so.
            fresh.setdefault("refresh_token", self._tok["refresh_token"])
            save_tokens(self.con, fresh)
            self._tok = load_tokens(self.con)
        return self._tok["access_token"]

    def _get(self, path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        import httpx

        delay = 2.0
        for _ in range(6):
            resp = httpx.get(
                f"{API}{path}",
                params=params,
                headers={"Authorization": f"Bearer {self._access()}"},
                timeout=30.0,
            )
            if resp.status_code == 429:  # rate limited — wait for the window, then retry
                wait = float(resp.headers.get("x-rate-limit-reset", 0)) - time.time()
                time.sleep(min(max(wait, delay), 900.0) if wait > 0 else delay)
                delay = min(delay * 2, 60.0)
                continue
            resp.raise_for_status()
            return resp.json()
        resp.raise_for_status()
        return resp.json()

    def me_id(self) -> str:
        return self._get("/users/me")["data"]["id"]

    def iter_bookmark_pages(self, max_results: int = 100) -> Iterator[dict[str, Any]]:
        """Yield raw v2 pages ({data, includes, meta}), newest-bookmarked first.
        `max_results` shrinks the page for capped fetches — X bills per post RETURNED, so a
        25-post free sync must not request a 100-post page (4x the cost for the same slice)."""
        uid = self.me_id()
        token = None
        while True:
            params = dict(_BOOKMARK_PARAMS)
            params["max_results"] = str(max(1, min(100, max_results)))
            if token:
                params["pagination_token"] = token
            page = self._get(f"/users/{uid}/bookmarks", params)
            if not page.get("data"):
                break
            yield page
            token = (page.get("meta") or {}).get("next_token")
            if not token:
                break
            time.sleep(0.5)


def backfill_via_api(con, client_id: str, incremental: bool = True,
                     max_total: int | None = None) -> int:
    """Pull bookmarks through the v2 API, upsert, and assign bm_rank (newest saved = highest).

    Incremental: stop once a whole page is already in the DB (newest-first ordering means
    we've caught up). `max_total` caps the tenant's TOTAL stored posts (the freemium slice:
    unpaid accounts import only their most-recent N). Returns the number of newly-added posts.
    """
    client = XApiClient(con, client_id)
    before = con.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
    total = before
    new_ids: list[str] = []   # newly-stored ids, in fetch (newest-first) order
    seen_ids: list[str] = []  # every id seen this run, in fetch order (for full re-rank)
    capped = max_total is not None and total >= max_total
    # Capped fetches request only what fits PLUS ONE: the +1 is the cheapest possible proof
    # that more bookmarks exist (library_more_exists → honest "you have more" upsell copy);
    # without it an exactly-filled page is ambiguous. Uncapped fetches use full pages.
    page_size = 100 if max_total is None else max(1, min(100, max_total - total + 1))
    for page in client.iter_bookmark_pages(page_size):
        if capped:
            break
        users = {u["id"]: u for u in (page.get("includes") or {}).get("users", [])}
        media = {m["media_key"]: m for m in (page.get("includes") or {}).get("media", [])}
        new_in_page = 0
        for tweet in page["data"]:
            rec = parse_bookmark_v2(tweet, users, media)
            if not rec["id"]:
                continue
            exists = con.execute("SELECT 1 FROM posts WHERE id = %s", (rec["id"],)).fetchone()
            if not exists and max_total is not None and total >= max_total:
                capped = True
                # We just SAW a bookmark we couldn't store — explicit proof the library has
                # more than the cap. Persist it so upsell copy can say "you have more"
                # honestly; a cap that fills exactly at a page boundary stays ambiguous
                # (X has no count API) and gets softer wording.
                con.execute(
                    "INSERT INTO sync_state (key, value) VALUES ('library_more_exists', '1') "
                    "ON CONFLICT (tenant_id, key) DO UPDATE SET value = '1'"
                )
                break  # free slice full — stop before storing more
            _upsert_post(con, rec)
            seen_ids.append(rec["id"])
            if not exists:
                new_in_page += 1
                total += 1
                new_ids.append(rec["id"])
        con.commit()
        # Stop BEFORE requesting another page once the entitlement is exactly full — each page
        # fetched is ~100 billed X-API reads, so overshooting by a page doubles a free user's cost.
        if max_total is not None and total >= max_total:
            capped = True
        if capped or (incremental and new_in_page == 0):
            break
    # bm_rank (bookmark order; higher = saved more recently):
    if not incremental and seen_ids:
        # Full page-through (e.g. post-upgrade import): we saw the whole timeline newest-first,
        # so re-rank everything seen to match — older posts fetched last must NOT outrank the
        # newest ones that were already stored from the free slice.
        for i, pid in enumerate(seen_ids):  # first fetched = newest = highest rank
            con.execute("UPDATE posts SET bm_rank = %s WHERE id = %s", (len(seen_ids) - i, pid))
        con.commit()
    elif new_ids:
        # Incremental top-up: everything new is newer than everything stored.
        base = con.execute("SELECT COALESCE(MAX(bm_rank), 0) FROM posts").fetchone()[0]
        for i, pid in enumerate(reversed(new_ids)):  # oldest-of-batch first
            con.execute("UPDATE posts SET bm_rank = %s WHERE id = %s", (base + 1 + i, pid))
        con.commit()
    return con.execute("SELECT COUNT(*) FROM posts").fetchone()[0] - before
