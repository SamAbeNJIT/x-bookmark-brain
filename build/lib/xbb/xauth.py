"""X OAuth 2.0 Authorization-Code-with-PKCE (public client — no secret).

The sanctioned path to a user's bookmarks: the user consents once, we get an access token
(+ refresh token via the offline.access scope), and call the v2 API server-side. We never
see their password or session cookies.

Pure helpers (PKCE, authorize-URL building) are unit-tested; the token HTTP calls need the
live endpoint and are exercised via the real connect flow.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from typing import Any

# Long-standing, stable OAuth2 endpoints (x.com domains also work; these are the documented ones).
AUTHORIZE_URL = "https://twitter.com/i/oauth2/authorize"
TOKEN_URL = "https://api.twitter.com/2/oauth2/token"
SCOPES = ["tweet.read", "users.read", "bookmark.read", "offline.access"]


def make_pkce() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) for PKCE S256."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode("ascii")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def make_state() -> str:
    return secrets.token_urlsafe(24)


def authorize_url(client_id: str, redirect_uri: str, state: str, challenge: str) -> str:
    from urllib.parse import urlencode

    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": " ".join(SCOPES),
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


def exchange_code(client_id: str, redirect_uri: str, code: str, verifier: str) -> dict[str, Any]:
    """Swap the authorization code for tokens (public client: client_id in body, no secret)."""
    import httpx

    resp = httpx.post(
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "code_verifier": verifier,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30.0,
    )
    resp.raise_for_status()
    return resp.json()  # {access_token, refresh_token, expires_in, token_type, scope}


def refresh_token(client_id: str, refresh: str) -> dict[str, Any]:
    """Get a fresh access token using the stored refresh token."""
    import httpx

    resp = httpx.post(
        TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh,
            "client_id": client_id,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30.0,
    )
    resp.raise_for_status()
    return resp.json()
