"""GitHub confidential OAuth App helpers."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
TOKEN_URL = "https://github.com/login/oauth/access_token"
USER_AGENT = "x-bookmark-brain/1.0"


def authorize_url(client_id: str, redirect_uri: str, state: str) -> str:
    return f"{AUTHORIZE_URL}?{urlencode({'client_id': client_id, 'redirect_uri': redirect_uri, 'state': state, 'scope': ''})}"


def exchange_code(client_id: str, client_secret: str, redirect_uri: str,
                  code: str, state: str) -> dict[str, Any]:
    import httpx

    response = httpx.post(
        TOKEN_URL,
        data={"client_id": client_id, "client_secret": client_secret, "code": code,
              "redirect_uri": redirect_uri, "state": state},
        headers={"Accept": "application/json", "User-Agent": USER_AGENT}, timeout=30.0,
    )
    response.raise_for_status()
    return response.json()


def fetch_me(access_token: str) -> dict[str, Any]:
    import httpx

    response = httpx.get(
        "https://api.github.com/user",
        headers={"Authorization": f"Bearer {access_token}",
                 "Accept": "application/vnd.github+json", "User-Agent": USER_AGENT},
        timeout=30.0,
    )
    response.raise_for_status()
    return response.json()
