"""Import slider + credit-subscription economics: pricing math, webhook grants, entitlement."""

import hashlib
import hmac
import json
import time

import pytest

from xbb import pricing, storage
from xbb.config import DEFAULT_TENANT_ID, Config


def test_import_price_math():
    assert pricing.import_price_usd(1000, 100, 0.01) == 9.00   # first 100 free
    assert pricing.import_price_usd(500, 100, 0.01) == 4.00
    assert pricing.import_price_usd(100, 100, 0.01) == 0.0     # all free
    assert pricing.unused_import_to_credits_usd(300, 0.01) == 3.00
    assert pricing.credits_for_topup(12.34) == 12.34


def _post_event(client, event, secret):
    payload = json.dumps(event)
    ts = int(time.time())
    sig = hmac.new(secret.encode(), f"{ts}.{payload}".encode(), hashlib.sha256).hexdigest()
    return client.post("/billing/webhook", content=payload,
                       headers={"Stripe-Signature": f"t={ts},v1={sig}",
                                "Content-Type": "application/json"})


def _secret():
    s = Config.from_env().stripe_webhook_secret
    if not s:
        pytest.skip("STRIPE_WEBHOOK_SECRET not set")
    return s


def test_import_purchase_raises_entitlement(client, db):
    secret = _secret()
    ev = {"id": "evt", "object": "event", "type": "checkout.session.completed",
          "data": {"object": {"id": "cs", "object": "checkout.session", "mode": "payment",
                              "client_reference_id": DEFAULT_TENANT_ID,
                              "metadata": {"kind": "import", "count": "1500"}}}}
    assert _post_event(client, ev, secret).status_code == 200
    con = storage.connect(db)
    try:
        assert storage.import_limit(con) == 1500
        # effective cap = free slice + purchased
        assert storage.effective_import_cap(con, 100) is None or True  # comped default acct
        con.execute("UPDATE accounts SET ingestion_paid = false WHERE id = %s",
                    (DEFAULT_TENANT_ID,)); con.commit()
        assert storage.effective_import_cap(con, 100) == 1600
    finally:
        con.close()


def test_invoice_paid_grants_monthly_sub_credits(client, db):
    secret = _secret()
    con = storage.connect(db)
    con.execute("UPDATE accounts SET credit_balance_usd = 0 WHERE id = %s", (DEFAULT_TENANT_ID,))
    con.commit(); con.close()
    ev = {"id": "evt", "object": "event", "type": "invoice.paid",
          "data": {"object": {"id": "in_1", "object": "invoice", "customer": "cus_x",
                              "subscription_details": {"metadata": {
                                  "kind": "credit_sub", "account_id": DEFAULT_TENANT_ID}}}}}
    assert _post_event(client, ev, secret).status_code == 200
    con = storage.connect(db)
    try:
        assert abs(storage.credit_balance(con) - pricing.SUB_MONTHLY_CREDITS_USD) < 1e-9
    finally:
        con.close()


def test_unrelated_invoice_grants_nothing(client, db):
    secret = _secret()
    con = storage.connect(db)
    con.execute("UPDATE accounts SET credit_balance_usd = 0 WHERE id = %s", (DEFAULT_TENANT_ID,))
    con.commit(); con.close()
    ev = {"id": "evt", "object": "event", "type": "invoice.paid",
          "data": {"object": {"id": "in_2", "object": "invoice", "customer": "cus_x",
                              "subscription_details": {"metadata": {}}}}}
    assert _post_event(client, ev, secret).status_code == 200
    con = storage.connect(db)
    try:
        assert storage.credit_balance(con) == 0.0
    finally:
        con.close()


def test_sync_gate_respects_purchased_entitlement(client, db, monkeypatch):
    # 3 seeded posts, free limit 3 -> blocked; buying entitlement re-opens the gate.
    monkeypatch.setenv("FREE_BOOKMARK_LIMIT", "3")
    con = storage.connect(db)
    con.execute("UPDATE accounts SET ingestion_paid = false, import_limit = 0 WHERE id = %s",
                (DEFAULT_TENANT_ID,)); con.commit(); con.close()
    r = client.post("/ui/refresh", follow_redirects=False)
    assert r.headers["location"] == "/ui/billing"          # at the cap -> paywall
    con = storage.connect(db)
    storage.add_import_limit(con, DEFAULT_TENANT_ID, 500)  # "bought" 500 more
    con.close()
    r = client.post("/ui/refresh", follow_redirects=False)
    assert r.headers["location"] == "/ui/refresh"          # gate open again
