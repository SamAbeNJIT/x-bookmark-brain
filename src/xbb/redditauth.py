"""OAuth for Reddit installed apps (Authorization Code + PKCE, no client secret)."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from . import xauth

make_pkce = xauth.make_pkce

AUTHORIZE_URL = "https://www.reddit.com/api/v1/authorize"
TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
SCOPES = ["read", "history"]
USER_AGENT = "web:x-bookmark-brain:v1.0 (bookmark library sync)"


def authorize_url(client_id: str, redirect_uri: str, state: str, challenge: str) -> str:
    return f"{AUTHORIZE_URL}?{urlencode({'client_id': client_id, 'response_type': 'code', 'state': state, 'redirect_uri': redirect_uri, 'duration': 'permanent', 'scope': ' '.join(SCOPES), 'code_challenge': challenge, 'code_challenge_method': 'S256'})}"


def exchange_code(
    client_id: str, redirect_uri: str, code: str, verifier: str
) -> dict[str, Any]:
    import httpx

    response = httpx.post(
        TOKEN_URL,
        auth=(client_id, ""),
        data={"grant_type": "authorization_code", "code": code,
              "redirect_uri": redirect_uri, "code_verifier": verifier},
        headers={"User-Agent": USER_AGENT}, timeout=30.0,
    )
    response.raise_for_status()
    return response.json()


def refresh_token(client_id: str, refresh: str) -> dict[str, Any]:
    import httpx

    response = httpx.post(
        TOKEN_URL, auth=(client_id, ""),
        data={"grant_type": "refresh_token", "refresh_token": refresh},
        headers={"User-Agent": USER_AGENT}, timeout=30.0,
    )
    response.raise_for_status()
    return response.json()


def fetch_me(access_token: str) -> dict[str, Any]:
    import httpx

    response = httpx.get(
        "https://oauth.reddit.com/api/v1/me",
        headers={"Authorization": f"Bearer {access_token}", "User-Agent": USER_AGENT},
        timeout=30.0,
    )
    response.raise_for_status()
    return response.json()
