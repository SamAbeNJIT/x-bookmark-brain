"""REQUIRE_AUTH gate: when on, protected routes demand a valid session; public ones don't."""

from fastapi.testclient import TestClient

from xbb import auth
from xbb.config import Config
from xbb.deps import SESSION_COOKIE, get_ai, get_db
from xbb.web import create_app


def _client(monkeypatch, seeded_db, fake_ai):
    monkeypatch.setenv("REQUIRE_AUTH", "true")
    app = create_app()

    def _db():
        from xbb import storage
        con = storage.connect(seeded_db)
        try:
            yield con
        finally:
            con.close()

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_ai] = lambda: fake_ai
    return TestClient(app)


def test_protected_route_redirects_without_session(monkeypatch, seeded_db, fake_ai):
    c = _client(monkeypatch, seeded_db, fake_ai)
    r = c.get("/ui/feed", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_public_routes_open_without_session(monkeypatch, seeded_db, fake_ai):
    c = _client(monkeypatch, seeded_db, fake_ai)
    assert c.get("/health").status_code == 200
    assert c.get("/login", follow_redirects=False).status_code == 200


def test_valid_session_passes_the_gate(monkeypatch, seeded_db, fake_ai):
    c = _client(monkeypatch, seeded_db, fake_ai)
    token = auth.make_session_token(Config.from_env().tenant_id, Config.from_env().session_secret)
    c.cookies.set(SESSION_COOKIE, token)
    r = c.get("/ui/feed", follow_redirects=False)
    assert r.status_code == 200


def test_source_connect_routes_are_never_public(monkeypatch, seeded_db, fake_ai):
    monkeypatch.setenv("REDDIT_CLIENT_ID", "reddit-client")
    c = _client(monkeypatch, seeded_db, fake_ai)
    for path in ("/connect/reddit/login", "/connect/reddit/callback?code=x&state=y"):
        r = c.get(path, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/login"
