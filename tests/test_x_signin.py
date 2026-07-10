"""Sign in with X: one tap creates the account, stores the token, and starts the free import."""

from urllib.parse import parse_qs, urlparse

import pytest

from xbb import jobs, storage, xauth


@pytest.fixture(autouse=True)
def _clean_accounts(db):
    """Accounts persist in the shared test DB — remove this suite's identities between tests."""
    def _purge():
        con = storage.connect(db)
        try:
            con.execute("DELETE FROM accounts WHERE x_user_id = '9001' "
                        "OR email = 'hoarder@example.com'")
            con.commit()
        finally:
            con.close()
    _purge()
    yield
    _purge()


@pytest.fixture
def fake_x(monkeypatch):
    """Stub the two live X calls (token exchange + /users/me) and record job starts."""
    started = []
    monkeypatch.setattr(xauth, "exchange_code", lambda cid, uri, code, ver: {
        "access_token": "at-123", "refresh_token": "rt-123", "expires_in": 7200})
    monkeypatch.setattr(xauth, "fetch_me", lambda access: {
        "id": "9001", "username": "adclicker", "name": "Ad Clicker"})
    monkeypatch.setattr(jobs, "start", lambda tid=None: started.append(tid) or True)
    return started


def _signin(client):
    """Drive the full flow: /oauth/signin -> extract state -> callback. Returns the response."""
    r = client.get("/oauth/signin", follow_redirects=False)
    assert r.status_code == 307
    loc = r.headers["location"]
    assert "twitter.com/i/oauth2/authorize" in loc
    state = parse_qs(urlparse(loc).query)["state"][0]
    assert state.startswith("si_")
    return client.get(f"/oauth/callback?code=abc&state={state}", follow_redirects=False)


def test_first_x_signin_creates_account_and_session_no_autosync(client, db, fake_x):
    r = _signin(client)
    assert r.status_code == 303 and r.headers["location"] == "/ui/refresh"
    assert "xbb_session" in r.headers.get("set-cookie", "")
    assert fake_x == []                     # owner's call: never sync without a button press
    con = storage.connect(db)
    try:
        row = con.execute(
            "SELECT id, x_handle, email FROM accounts WHERE x_user_id = '9001'").fetchone()
        assert row is not None and row[1] == "adclicker" and row[2] is None
        # the bookmark token was stored under THEIR tenant (one-tap connect)
        tok = con.execute(
            "SELECT value FROM sync_state WHERE tenant_id = %s AND key = 'x_oauth'",
            (row[0],)).fetchone()
        assert tok is not None
    finally:
        con.close()


def test_second_x_signin_reuses_account(client, db, fake_x):
    _signin(client)
    r = _signin(client)                      # same X identity signs in again
    assert r.status_code == 303 and r.headers["location"] == "/"   # returning user -> home
    con = storage.connect(db)
    try:
        n = con.execute("SELECT COUNT(*) FROM accounts WHERE x_user_id = '9001'").fetchone()[0]
        assert n == 1                        # no duplicate account
    finally:
        con.close()
    assert fake_x == []                      # still no un-asked-for syncs


def test_x_signin_links_to_existing_email_account(client, db, fake_x):
    con = storage.connect(db)
    try:
        acct = storage.get_or_create_account(con, "hoarder@example.com")
        storage.set_account_x_identity(con, acct, "9001", "adclicker")
    finally:
        con.close()
    r = _signin(client)                      # X identity already linked to the email account
    assert r.status_code == 303
    con = storage.connect(db)
    try:
        n = con.execute("SELECT COUNT(*) FROM accounts WHERE x_user_id = '9001'").fetchone()[0]
        assert n == 1                        # landed in the linked account, no new one
        email = con.execute(
            "SELECT email FROM accounts WHERE x_user_id = '9001'").fetchone()[0]
        assert email == "hoarder@example.com"
    finally:
        con.close()
    assert fake_x == []                      # existing account -> no auto-import


def test_cancelled_signin_returns_to_login(client, fake_x):
    r = client.get("/oauth/callback?state=si_whatever&error=access_denied",
                   follow_redirects=False)
    assert r.status_code == 200 and "cancelled" in r.text


def test_first_run_sync_page_adapts_to_login_method(client, db, fake_x, monkeypatch):
    from xbb import xapi as xapi_mod
    con = storage.connect(db)
    try:  # first-run = zero posts; the client fixture seeds a few, so clear them
        con.execute("DELETE FROM posts"); con.commit()
    finally:
        con.close()
    # Magic-link user (not connected): the page routes them to Connect X, no sync button.
    monkeypatch.setattr(xapi_mod, "is_connected", lambda con: False)
    r = client.get("/ui/refresh")
    assert "Connect X" in r.text and "Sync now" not in r.text
    # X-sign-in user (already connected): free-100 offer with an explicit button, no auto-run.
    monkeypatch.setattr(xapi_mod, "is_connected", lambda con: True)
    r = client.get("/ui/refresh")
    assert "free" in r.text.lower() and "Sync my first" in r.text
    assert fake_x == []                      # rendering the page never starts a job
