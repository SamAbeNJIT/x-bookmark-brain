"""GitHub starred-repository adapter."""

from __future__ import annotations

import time
from typing import Any, Iterator

from . import githubauth, sources

API = "https://api.github.com"
TOKEN_KEY = "github_oauth"


def record_id(native_id: str) -> str:
    return sources.record_id("gh", native_id)


def parse_star(item: dict[str, Any]) -> dict[str, Any] | None:
    repo = item.get("repo") if isinstance(item, dict) else None
    if not isinstance(repo, dict) or repo.get("id") is None or not item.get("starred_at"):
        return None
    owner = repo.get("owner") or {}
    topics = repo.get("topics") or []
    text = (f"{repo.get('full_name') or ''}\n{repo.get('description') or ''}\n"
            f"{' '.join(topics)}")
    author = None
    if owner.get("id") is not None:
        author = {"id": f"gh-user-{owner['id']}", "handle": owner.get("login"),
                  "display_name": owner.get("login"), "avatar_url": owner.get("avatar_url")}
    url = repo.get("html_url")
    return {
        "id": record_id(str(repo["id"])), "sort_index": None, "url": url, "text": text,
        "lang": None, "created_at": item["starred_at"], "bookmarked_at": item["starred_at"],
        "author": author, "source": "github", "kind": "original", "parent_post_id": None,
        "parent": None, "media": [], "hashtags": topics,
        "links": [{"url": url}] if url else [], "like_count": repo.get("stargazers_count"),
        "repost_count": None, "raw": item,
    }


class GitHubApiClient:
    def __init__(self, con) -> None:
        tokens = sources.load_tokens(con, TOKEN_KEY)
        if not tokens:
            raise RuntimeError("Not connected to GitHub — use the Connect GitHub flow first.")
        self.access_token = tokens["access_token"]

    def _get(self, url: str) -> tuple[list[dict[str, Any]], str | None]:
        import httpx

        delay = 2.0
        for _ in range(6):
            response = httpx.get(
                url,
                headers={"Authorization": f"Bearer {self.access_token}",
                         "Accept": "application/vnd.github.star+json",
                         "User-Agent": githubauth.USER_AGENT}, timeout=30.0,
            )
            if response.status_code == 429 or (
                response.status_code == 403 and response.headers.get("x-ratelimit-remaining") == "0"
            ):
                reset = float(response.headers.get("x-ratelimit-reset", 0) or 0) - time.time()
                retry_after = float(response.headers.get("retry-after", 0) or 0)
                wait = max(reset, retry_after, delay)
                time.sleep(min(wait, 900.0))
                delay = min(delay * 2, 60.0)
                continue
            response.raise_for_status()
            return response.json(), (response.links.get("next") or {}).get("url")
        response.raise_for_status()
        return response.json(), (response.links.get("next") or {}).get("url")

    def iter_starred_pages(self) -> Iterator[list[dict[str, Any]]]:
        url = f"{API}/user/starred?per_page=100"
        while url:
            page, url = self._get(url)
            if not page:
                break
            yield page


def backfill(con, cfg, *, incremental: bool, max_total: int | None,
             client: GitHubApiClient | None = None) -> int:
    client = client or GitHubApiClient(con)
    return sources.backfill_pages(
        con, "github", client.iter_starred_pages(), parse_star,
        incremental=incremental, max_total=max_total,
    )


class GitHubAdapter:
    name = "github"

    @staticmethod
    def is_configured(cfg) -> bool:
        return bool(cfg.github_client_id and cfg.github_client_secret)

    @staticmethod
    def is_connected(con) -> bool:
        return sources.is_connected(con, TOKEN_KEY)

    @staticmethod
    def record_id(native_id: str) -> str:
        return record_id(native_id)

    @staticmethod
    def authorize_url(cfg, con, state: str) -> str:
        return githubauth.authorize_url(cfg.github_client_id, cfg.github_redirect_uri, state)

    @staticmethod
    def handle_callback(cfg, con, code: str, state: str) -> None:
        if not code:
            raise ValueError("GitHub connection expired or invalid")
        tokens = githubauth.exchange_code(cfg.github_client_id, cfg.github_client_secret,
                                          cfg.github_redirect_uri, code, state)
        me = githubauth.fetch_me(tokens["access_token"])
        tokens["username"] = me.get("login")
        tokens["user_id"] = me.get("id")
        sources.save_tokens(con, TOKEN_KEY, tokens, cfg=cfg)

    @staticmethod
    def backfill(con, cfg, *, incremental: bool, max_total: int | None) -> int:
        return backfill(con, cfg, incremental=incremental, max_total=max_total)


ADAPTER = sources.register(GitHubAdapter())
