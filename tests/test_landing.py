"""Landing page: anonymous visitors in hosted mode see the pitch; signed-in users see the app."""

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


def test_anonymous_sees_landing_page(monkeypatch, seeded_db, fake_ai):
    c = _client(monkeypatch, seeded_db, fake_ai)
    r = c.get("/", follow_redirects=False)
    assert r.status_code == 200
    assert "Start free" in r.text            # the pitch, not a login box
    assert "/static/feed.png" in r.text
    assert "/terms" in r.text


def test_signed_in_sees_the_app_home(monkeypatch, seeded_db, fake_ai):
    c = _client(monkeypatch, seeded_db, fake_ai)
    cfg = Config.from_env()
    c.cookies.set(SESSION_COOKIE, auth.make_session_token(cfg.tenant_id, cfg.session_secret))
    r = c.get("/", follow_redirects=False)
    assert r.status_code == 200
    assert "bookmarks" in r.text and "Start free" not in r.text


def test_static_screenshots_served(monkeypatch, seeded_db, fake_ai):
    c = _client(monkeypatch, seeded_db, fake_ai)
    assert c.get("/static/feed.png").status_code == 200  # auth-exempt + mounted
