"""Pure unit tests for auth.py — no DB, no DATABASE_URL, no fixtures.

Exercises the signed-token round-trips and the rejection paths (tampered, wrong secret,
expired, cross-kind). Nothing here touches storage or the app.
"""

from xbb.auth import (
    make_login_token,
    make_session_token,
    verify_login_token,
    verify_session_token,
)

SECRET = "unit-test-signing-secret"


def test_login_token_round_trip():
    token = make_login_token("alice@example.com", SECRET)
    assert verify_login_token(token, SECRET) == "alice@example.com"


def test_login_token_rejects_tampering():
    token = make_login_token("alice@example.com", SECRET)
    tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
    assert verify_login_token(tampered, SECRET) is None


def test_login_token_rejects_wrong_secret():
    token = make_login_token("alice@example.com", SECRET)
    assert verify_login_token(token, "a-different-secret") is None


def test_login_token_rejects_expired():
    token = make_login_token("alice@example.com", SECRET)
    # max_age of -1s means "must be newer than -1 seconds" — no token can satisfy that,
    # so this deterministically exercises the SignatureExpired → None path without sleeping.
    assert verify_login_token(token, SECRET, max_age_s=-1) is None


def test_session_token_round_trip():
    token = make_session_token("acct_123", SECRET)
    assert verify_session_token(token, SECRET) == "acct_123"


def test_session_token_rejects_expired():
    token = make_session_token("acct_123", SECRET)
    assert verify_session_token(token, SECRET, max_age_s=-1) is None


def test_tokens_are_not_interchangeable():
    # Distinct salts: a login token must not verify as a session token, and vice-versa.
    login = make_login_token("alice@example.com", SECRET)
    session = make_session_token("acct_1", SECRET)
    assert verify_session_token(login, SECRET) is None
    assert verify_login_token(session, SECRET) is None
