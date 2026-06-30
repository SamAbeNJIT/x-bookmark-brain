"""Magic-link login + session token logic — pure, stateless, unit-testable.

Stateless signed tokens via itsdangerous. No DB, no email sending, no env reads: the caller
always passes the signing secret in. Two token kinds, separated by salt so one can never be
used as the other:

  - **login token**  — short-lived (15 min), carries the email a magic link was sent to.
  - **session token** — long-lived (30 days), carries the authenticated account id.

The signature guarantees integrity (a tampered token is rejected) and the embedded timestamp
guarantees expiry. Tokens are signed, *not* encrypted — so the payload is readable; only put
non-secret values in it (an email / account id, which is exactly what we carry).
"""

from __future__ import annotations

from itsdangerous import BadData, URLSafeTimedSerializer

# Distinct salts namespace the two token kinds: a login token will fail to verify as a
# session token (different salt → signature mismatch), and vice-versa.
_LOGIN_SALT = "xbb-login-link"
_SESSION_SALT = "xbb-session"

# Default token lifetimes, in seconds.
LOGIN_MAX_AGE_S = 900            # 15 minutes
SESSION_MAX_AGE_S = 2_592_000    # 30 days


def _serializer(secret: str, salt: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(secret_key=secret, salt=salt)


def make_login_token(email: str, secret: str) -> str:
    """Sign a time-limited magic-link token carrying `email` (verify with verify_login_token)."""
    return _serializer(secret, _LOGIN_SALT).dumps(email)


def verify_login_token(token: str, secret: str, max_age_s: int = LOGIN_MAX_AGE_S) -> str | None:
    """Return the email from a valid login token, or None if invalid / tampered / expired."""
    try:
        return _serializer(secret, _LOGIN_SALT).loads(token, max_age=max_age_s)
    except BadData:  # covers BadSignature and SignatureExpired
        return None


def make_session_token(account_id: str, secret: str) -> str:
    """Sign a long-lived session token carrying `account_id`."""
    return _serializer(secret, _SESSION_SALT).dumps(account_id)


def verify_session_token(token: str, secret: str, max_age_s: int = SESSION_MAX_AGE_S) -> str | None:
    """Return the account_id from a valid session token, or None if invalid / tampered / expired."""
    try:
        return _serializer(secret, _SESSION_SALT).loads(token, max_age=max_age_s)
    except BadData:
        return None
