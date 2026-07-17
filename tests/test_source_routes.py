from urllib.parse import parse_qs, urlsplit

from xbb import auth, githubauth, redditauth, sources
from xbb.config import Config
from xbb.deps import SESSION_COOKIE


def test_reddit_login_callback_stores_token(client, monkeypatch, seeded_db):
    monkeypatch.setenv("REDDIT_CLIENT_ID", "reddit-client")
    monkeypatch.setattr(redditauth, "exchange_code",
                        lambda client_id, redirect_uri, code, verifier:
                        {"access_token": "reddit-access", "refresh_token": "refresh",
                         "expires_in": 3600})
    monkeypatch.setattr(redditauth, "fetch_me", lambda token: {"name": "alice"})
    login = client.get("/connect/reddit/login", follow_redirects=False)
    assert login.status_code == 307
    query = parse_qs(urlsplit(login.headers["location"]).query)
    callback = client.get("/connect/reddit/callback",
                          params={"code": "code", "state": query["state"][0]},
                          follow_redirects=False)
    assert callback.status_code == 303 and callback.headers["location"] == "/ui/refresh"
    from xbb import storage
    con = storage.connect(seeded_db)
    try:
        assert sources.load_tokens(con, "reddit_oauth")["username"] == "alice"
    finally:
        con.close()


def test_github_login_callback_stores_token(client, monkeypatch, seeded_db):
    monkeypatch.setenv("GITHUB_CLIENT_ID", "github-client")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "github-secret")
    monkeypatch.setattr(githubauth, "exchange_code",
                        lambda client_id, client_secret, redirect_uri, code, state:
                        {"access_token": "github-access"})
    monkeypatch.setattr(githubauth, "fetch_me", lambda token: {"login": "octo", "id": 7})
    login = client.get("/connect/github/login", follow_redirects=False)
    query = parse_qs(urlsplit(login.headers["location"]).query)
    callback = client.get("/connect/github/callback",
                          params={"code": "code", "state": query["state"][0]},
                          follow_redirects=False)
    assert callback.status_code == 303
    from xbb import storage
    con = storage.connect(seeded_db)
    try:
        assert sources.load_tokens(con, "github_oauth")["username"] == "octo"
    finally:
        con.close()


def test_callback_rejects_state_for_another_session_tenant(client, monkeypatch):
    monkeypatch.setenv("REDDIT_CLIENT_ID", "reddit-client")
    cfg = Config.from_env()
    state = sources.make_oauth_state("reddit", "00000000-0000-0000-0000-00000000aaaa",
                                     cfg.session_secret)
    token = auth.make_session_token("00000000-0000-0000-0000-00000000bbbb", cfg.session_secret)
    client.cookies.set(SESSION_COOKIE, token)
    response = client.get("/connect/reddit/callback", params={"code": "x", "state": state})
    assert response.status_code == 400
