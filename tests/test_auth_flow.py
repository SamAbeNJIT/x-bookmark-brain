"""Integration tests for the mounted auth routes (the magic-link flow end to end)."""

from xbb import auth
from xbb.config import Config


def test_login_page_renders(client):
    r = client.get("/login")
    assert r.status_code == 200
    assert 'action="/auth/request"' in r.text


def test_request_shows_check_email(client):
    r = client.post("/auth/request", data={"email": "a@b.com"})
    assert r.status_code == 200
    assert "Check your email" in r.text


def test_verify_valid_token_sets_session_cookie(client):
    secret = Config.from_env().session_secret
    token = auth.make_login_token("new@example.com", secret)
    r = client.get(f"/auth/verify?token={token}", follow_redirects=False)
    assert r.status_code == 303
    assert "xbb_session" in r.headers.get("set-cookie", "")


def test_verify_rejects_bad_token(client):
    r = client.get("/auth/verify?token=not-a-real-token", follow_redirects=False)
    assert r.status_code == 200
    assert "invalid or expired" in r.text
