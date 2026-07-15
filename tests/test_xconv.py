"""Privacy-minimal X Ads conversion attribution (xconv): first-party twclid capture,
one server-side registration event per NEW account, idempotent, never blocking signup."""

import json
import logging
from urllib.parse import parse_qs, urlparse

import pytest

from xbb import storage, xauth, xconv
from xbb.config import Config
from xbb.log import logger as xbb_logger

ADS_ENV = {
    "X_ADS_PIXEL_ID": "tw-pixel", "X_ADS_EVENT_ID": "tw-pixel-event",
    "X_ADS_CONSUMER_KEY": "ck", "X_ADS_CONSUMER_SECRET": "cs",
    "X_ADS_ACCESS_TOKEN": "at", "X_ADS_ACCESS_SECRET": "ats",
}


@pytest.fixture
def ads_cfg(monkeypatch):
    for k, v in ADS_ENV.items():
        monkeypatch.setenv(k, v)
    return Config.from_env()


@pytest.fixture
def log_capture():
    seen: list[str] = []
    h = logging.Handler()
    h.emit = lambda r: seen.append(r.getMessage())
    xbb_logger.addHandler(h)
    yield seen
    xbb_logger.removeHandler(h)


# ------------------------------------------------------------------- twclid capture

def test_twclid_param_sets_first_party_cookie(client):
    r = client.get("/", params={"twclid": "click123"})
    assert "xbb_twclid=click123" in r.headers.get("set-cookie", "")


def test_empty_twclid_never_overwrites(client):
    client.cookies.set("xbb_twclid", "keepme")
    r = client.get("/", params={"twclid": ""})
    assert "xbb_twclid" not in r.headers.get("set-cookie", "")
    r = client.get("/")  # absent param: also no overwrite
    assert "xbb_twclid" not in r.headers.get("set-cookie", "")


# ------------------------------------------------------------------- the send job

def _run_job(monkeypatch, db, ads_cfg, twclid, account_id=None, post=None):
    from xbb.config import DEFAULT_TENANT_ID
    account_id = account_id or DEFAULT_TENANT_ID
    calls = []
    monkeypatch.setattr(xconv, "_post", post or (lambda cfg, payload: calls.append(payload) or 200))
    # point the job's own connection at the test DB
    monkeypatch.setattr(ads_cfg.__class__, "app_database_url", ads_cfg.app_database_url, raising=False)
    xconv._send_job(ads_cfg, account_id, twclid)
    return calls


def test_send_job_sends_minimal_payload_once(db, ads_cfg, monkeypatch, log_capture):
    calls = _run_job(monkeypatch, db, ads_cfg, "click123")
    assert len(calls) == 1
    conv = calls[0]["conversions"][0]
    assert conv["identifiers"] == [{"twclid": "click123"}]
    assert conv["event_id"] == "tw-pixel-event"
    assert set(conv) == {"conversion_time", "event_id", "identifiers", "conversion_id"}
    assert any(m.startswith("xconv.sent") and "twclid_available=true" in m for m in log_capture)
    # duplicate callback: marker already claimed -> zero additional sends
    calls2 = _run_job(monkeypatch, db, ads_cfg, "click123")
    assert calls2 == []


def test_send_job_without_twclid_skips_send(db, ads_cfg, monkeypatch, log_capture):
    calls = _run_job(monkeypatch, db, ads_cfg, None)
    assert calls == []
    assert any("twclid_available=false" in m for m in log_capture)


def test_send_job_failure_never_raises_and_marks_failed(db, ads_cfg, monkeypatch, log_capture):
    monkeypatch.setattr(xconv, "_RETRIES", 2)
    monkeypatch.setattr(xconv.time, "sleep", lambda s: None)

    def _boom(cfg, payload):
        raise RuntimeError("x api down")
    _run_job(monkeypatch, db, ads_cfg, "click123", post=_boom)  # must not raise
    con = storage.connect(db)
    try:
        marker = json.loads(con.execute(
            "SELECT value FROM sync_state WHERE key = %s", (xconv.MARKER_KEY,)).fetchone()[0])
        assert marker["status"] == "failed"
        assert "click123" not in json.dumps(marker)  # twclid never persisted server-side
    finally:
        con.close()
    assert any(m.startswith("xconv.failed") for m in log_capture)


def test_unconfigured_fire_is_a_safe_noop(db, log_capture):
    cfg = Config.from_env()  # no X_ADS_* vars
    xconv.fire_registration(cfg, "some-account", "click123")
    assert any("xconv.skipped reason=unconfigured" in m for m in log_capture)


# ------------------------------------------------------------------- full sign-in flow

@pytest.fixture
def fake_x(monkeypatch):
    monkeypatch.setattr(xauth, "exchange_code", lambda cid, uri, code, ver: {
        "access_token": "at-1", "refresh_token": "rt-1", "expires_in": 7200})
    monkeypatch.setattr(xauth, "fetch_me", lambda access: {
        "id": "77001", "username": "adconvert", "name": "Ad Convert"})
    from xbb import jobs
    monkeypatch.setattr(jobs, "start", lambda tid=None: True)


def _signin(client):
    r = client.get("/oauth/signin", follow_redirects=False)
    state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
    return client.get(f"/oauth/callback?code=abc&state={state}", follow_redirects=False)


def test_new_x_signup_fires_registration_with_cookie_twclid(client, db, fake_x, monkeypatch):
    for k, v in ADS_ENV.items():
        monkeypatch.setenv(k, v)
    fired = []
    monkeypatch.setattr(xconv, "fire_registration",
                        lambda cfg, acct, twclid: fired.append((acct, twclid)))
    client.cookies.set(xconv.TWCLID_COOKIE, "click-from-ad")
    r = _signin(client)
    assert r.status_code == 303
    assert len(fired) == 1 and fired[0][1] == "click-from-ad"
    # second sign-in, same X identity: existing account -> NO registration event
    _signin(client)
    assert len(fired) == 1
    con = storage.connect(db)
    con.execute("DELETE FROM accounts WHERE x_user_id = '77001'"); con.commit(); con.close()


def test_signup_without_twclid_still_works(client, db, fake_x, monkeypatch):
    for k, v in ADS_ENV.items():
        monkeypatch.setenv(k, v)
    fired = []
    monkeypatch.setattr(xconv, "fire_registration",
                        lambda cfg, acct, twclid: fired.append(twclid))
    r = _signin(client)
    assert r.status_code == 303 and "xbb_session" in r.headers.get("set-cookie", "")
    assert fired == [None]  # fired with no twclid -> job will log twclid_available=false
    con = storage.connect(db)
    con.execute("DELETE FROM accounts WHERE x_user_id = '77001'"); con.commit(); con.close()
