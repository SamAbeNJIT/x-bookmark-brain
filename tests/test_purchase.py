"""One-time purchases: the webhook marks ingestion paid / tops up credits; sync is gated."""

import hashlib
import hmac
import json
import time

import pytest

from xbb import storage
from xbb.config import DEFAULT_TENANT_ID, Config


def _secret():
    s = Config.from_env().stripe_webhook_secret
    if not s:
        pytest.skip("STRIPE_WEBHOOK_SECRET not set")
    return s


def _post_event(client, event: dict, secret: str):
    payload = json.dumps(event)
    ts = int(time.time())
    sig = hmac.new(secret.encode(), f"{ts}.{payload}".encode(), hashlib.sha256).hexdigest()
    return client.post("/billing/webhook", content=payload,
                       headers={"Stripe-Signature": f"t={ts},v1={sig}",
                                "Content-Type": "application/json"})


def _session_event(**obj):
    return {"id": "evt", "object": "event", "type": "checkout.session.completed",
            "data": {"object": {"id": "cs", "object": "checkout.session", **obj}}}


def test_ingestion_payment_marks_paid(client, db):
    secret = _secret()
    con = storage.connect(db)
    con.execute("UPDATE accounts SET ingestion_paid = false WHERE id = %s", (DEFAULT_TENANT_ID,))
    con.commit(); con.close()
    ev = _session_event(mode="payment", client_reference_id=DEFAULT_TENANT_ID,
                        metadata={"kind": "ingestion"})
    assert _post_event(client, ev, secret).status_code == 200
    con = storage.connect(db)
    try:
        assert storage.is_ingestion_paid(con) is True
    finally:
        con.close()


def test_credit_payment_adds_balance(client, db):
    secret = _secret()
    con = storage.connect(db)
    con.execute("UPDATE accounts SET credit_balance_usd = 0 WHERE id = %s", (DEFAULT_TENANT_ID,))
    con.commit(); con.close()
    ev = _session_event(mode="payment", client_reference_id=DEFAULT_TENANT_ID,
                        amount_total=1000, metadata={"kind": "credits"})  # $10.00
    assert _post_event(client, ev, secret).status_code == 200
    con = storage.connect(db)
    try:
        assert abs(storage.credit_balance(con) - 10.0) < 1e-9
    finally:
        con.close()


def test_sync_blocked_until_ingestion_paid(client, db):
    con = storage.connect(db)
    con.execute("UPDATE accounts SET ingestion_paid = false WHERE id = %s", (DEFAULT_TENANT_ID,))
    con.commit(); con.close()
    r = client.post("/ui/refresh", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/ui/billing"
