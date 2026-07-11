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


def test_seo_plumbing_is_public_and_wellformed(monkeypatch, seeded_db, fake_ai):
    c = _client(monkeypatch, seeded_db, fake_ai)  # REQUIRE_AUTH on: these must stay public
    r = c.get("/sitemap.xml", follow_redirects=False)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/xml")
    for p in ("https://x-bookmarks.ai/", "/terms", "/privacy"):
        assert p in r.text
    r = c.get("/robots.txt", follow_redirects=False)
    assert r.status_code == 200
    assert "Sitemap: https://x-bookmarks.ai/sitemap.xml" in r.text
    assert "Disallow: /ui/" in r.text


def test_landing_has_seo_head_tags(monkeypatch, seeded_db, fake_ai):
    c = _client(monkeypatch, seeded_db, fake_ai)
    html = c.get("/").text
    assert '<link rel="canonical" href="https://x-bookmarks.ai/">' in html
    assert 'property="og:title"' in html and 'name="twitter:card"' in html
    assert 'application/ld+json' in html and '"@type":"SoftwareApplication"' in html


def test_feedback_form_submits(client):
    r = client.get("/ui/feedback")
    assert r.status_code == 200 and 'action="/ui/feedback"' in r.text
    r = client.post("/ui/feedback", data={"message": "launch-night test feedback"})
    assert r.status_code == 200
    assert "thank you" in r.text.lower()  # alert email is best-effort console-log in tests
