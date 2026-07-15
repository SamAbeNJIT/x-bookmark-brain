"""Privacy-minimal X Ads conversion attribution (server-side Conversion API only).

NO JavaScript pixel, no third-party scripts, no cross-site cookies. The flow:
  1. An X ad click lands with ?twclid=... → a first-party cookie remembers it (30 days,
     the standard click-attribution window; an empty param never overwrites a real one).
  2. When (and only when) a genuinely NEW account is created, ONE CompleteRegistration-type
     event goes server-to-server to the X Conversion API: the twclid, a timestamp, the
     configured event id, and the account uuid as `conversion_id` (X-side dedup). Nothing
     else — no email, handle, content, or browsing data.
  3. Idempotency is DB-backed: a per-tenant sync_state marker is claimed with INSERT ON
     CONFLICT DO NOTHING before any send, so duplicate callbacks/retries can't double-fire.
  4. Retention: the twclid lives in the user's own cookie and in-process during the send
     attempts; it is never persisted server-side (the marker stores only status/ids).

Fails safe: unconfigured → skipped with a log line; X API down → bounded retries in a
daemon thread, then a `failed` marker — account creation is never blocked or broken.
Endpoint: POST https://ads-api.x.com/12/measurement/conversions/{pixel_id} (OAuth 1.0a).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import threading
import time
import urllib.parse
from datetime import datetime, timezone

from .log import logger

TWCLID_COOKIE = "xbb_twclid"
TWCLID_MAX_AGE = 30 * 24 * 3600  # X's standard click-attribution window
MARKER_KEY = "x_conv_registration"
_RETRIES = 3


def _oauth1_header(method: str, url: str, ck: str, cs: str, at: str, ats: str) -> str:
    """Minimal OAuth 1.0a HMAC-SHA1 header (JSON-body requests sign only the oauth params)."""
    enc = lambda s: urllib.parse.quote(str(s), safe="~")  # noqa: E731
    p = {
        "oauth_consumer_key": ck,
        "oauth_nonce": secrets.token_hex(16),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(time.time())),
        "oauth_token": at,
        "oauth_version": "1.0",
    }
    param_str = "&".join(f"{enc(k)}={enc(v)}" for k, v in sorted(p.items()))
    base = "&".join([method.upper(), enc(url), enc(param_str)])
    key = f"{enc(cs)}&{enc(ats)}".encode()
    p["oauth_signature"] = base64.b64encode(
        hmac.new(key, base.encode(), hashlib.sha1).digest()).decode()
    return "OAuth " + ", ".join(f'{enc(k)}="{enc(v)}"' for k, v in sorted(p.items()))


def _configured(cfg) -> bool:
    return all((cfg.x_ads_pixel_id, cfg.x_ads_event_id, cfg.x_ads_consumer_key,
                cfg.x_ads_consumer_secret, cfg.x_ads_access_token, cfg.x_ads_access_secret))


def _post(cfg, payload: dict) -> int:  # pragma: no cover — network edge, faked in tests
    import httpx

    url = f"https://ads-api.x.com/12/measurement/conversions/{cfg.x_ads_pixel_id}"
    resp = httpx.post(
        url, json=payload, timeout=15.0,
        headers={"Authorization": _oauth1_header(
            "POST", url, cfg.x_ads_consumer_key, cfg.x_ads_consumer_secret,
            cfg.x_ads_access_token, cfg.x_ads_access_secret)})
    resp.raise_for_status()
    return resp.status_code


def _claim_marker(con, status: str, conversion_id: str) -> bool:
    """Claim the one-registration-event slot for this tenant. False = already claimed."""
    row = con.execute(
        "INSERT INTO sync_state (key, value) VALUES (%s, %s) "
        "ON CONFLICT (tenant_id, key) DO NOTHING RETURNING key",
        (MARKER_KEY, json.dumps({"status": status, "conversion_id": conversion_id,
                                 "ts": datetime.now(timezone.utc).isoformat()})),
    ).fetchone()
    con.commit()
    return row is not None


def _set_marker(con, status: str, conversion_id: str) -> None:
    con.execute(
        "UPDATE sync_state SET value = %s WHERE key = %s",
        (json.dumps({"status": status, "conversion_id": conversion_id,
                     "ts": datetime.now(timezone.utc).isoformat()}), MARKER_KEY))
    con.commit()


def _send_job(cfg, account_id: str, twclid: str | None) -> None:
    """The actual send: claim marker → send with bounded retries → record outcome.
    Runs in a daemon thread in prod; called directly by tests. Never raises."""
    from . import storage

    try:
        con = storage.connect(cfg.app_database_url, account_id)
    except Exception:
        logger.exception("xconv.db_unavailable tenant=%s", account_id)
        return
    try:
        if not _claim_marker(con, "pending", account_id):
            logger.info("xconv.duplicate_suppressed tenant=%s", account_id)
            return
        if not twclid:
            _set_marker(con, "no_twclid", account_id)
            logger.info("xconv.skipped tenant=%s twclid_available=false", account_id)
            return
        payload = {"conversions": [{
            "conversion_time": datetime.now(timezone.utc).isoformat(timespec="milliseconds")
                               .replace("+00:00", "Z"),
            "event_id": cfg.x_ads_event_id,
            "identifiers": [{"twclid": twclid}],
            "conversion_id": account_id,  # X-side dedup key, matches our marker
        }]}
        delay = 1.0
        for attempt in range(1, _RETRIES + 1):
            try:
                status = _post(cfg, payload)
                _set_marker(con, "sent", account_id)
                logger.info("xconv.sent tenant=%s status=%s attempt=%d twclid_available=true",
                            account_id, status, attempt)
                return
            except Exception as e:  # never log tokens — e carries only the HTTP failure
                logger.warning("xconv.attempt_failed tenant=%s attempt=%d err=%s",
                               account_id, attempt, type(e).__name__)
                time.sleep(delay)
                delay *= 2
        _set_marker(con, "failed", account_id)  # twclid deliberately NOT persisted
        logger.error("xconv.failed tenant=%s attempts=%d", account_id, _RETRIES)
    except Exception:
        logger.exception("xconv.unexpected tenant=%s", account_id)
    finally:
        con.close()


def fire_registration(cfg, account_id: str, twclid: str | None) -> None:
    """Fire-and-forget registration conversion for a genuinely NEW account. Never blocks
    or raises; unconfigured deployments skip with a log line."""
    if not _configured(cfg):
        logger.info("xconv.skipped reason=unconfigured tenant=%s", account_id)
        return
    threading.Thread(target=_send_job, args=(cfg, account_id, twclid), daemon=True).start()
