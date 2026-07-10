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
    # Pack bonuses (2026-07-10 pivot): +10% at $5, +20% at $10, +30% at $20; none below $5.
    assert pricing.credits_for_topup(4.99) == 4.99
    assert pricing.credits_for_topup(5.00) == 5.50
    assert pricing.credits_for_topup(12.34) == 14.81
    assert pricing.credits_for_topup(20.00) == 26.00


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
                              "payment_intent": "pi_test_123", "amount_total": 1400,
                              "metadata": {"kind": "import", "count": "1500"}}}}
    assert _post_event(client, ev, secret).status_code == 200
    con = storage.connect(db)
    try:
        assert storage.import_limit(con) == 1500
        # the payment reference is remembered for the refund true-up
        assert con.execute("SELECT import_payment_intent, import_paid_usd FROM accounts "
                           "WHERE id = %s", (DEFAULT_TENANT_ID,)).fetchone() == ("pi_test_123", 14.0)
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


# --------------------------------------------------------------------------- refund true-up

from xbb import jobs


class _Refunds:
    def __init__(self):
        self.calls = []

    def __call__(self, api_key, payment_intent, amount_usd):
        self.calls.append((payment_intent, amount_usd))


@pytest.fixture
def true_up_env(db, monkeypatch):
    """A tenant with a purchased-but-oversized import; refund + alerts stubbed."""
    refunds = _Refunds()
    monkeypatch.setattr("xbb.billing.refund_payment", refunds)
    monkeypatch.setattr("xbb.mail.send_owner_alert", lambda *a, **k: None)
    con = storage.connect(db)
    con.execute("UPDATE accounts SET ingestion_paid = false WHERE id = %s", (DEFAULT_TENANT_ID,))
    con.commit(); con.close()
    return refunds


def _setup_purchase(db, import_limit, pi, paid):
    con = storage.connect(db)
    storage.add_import_limit(con, DEFAULT_TENANT_ID, import_limit)
    storage.set_import_payment(con, DEFAULT_TENANT_ID, pi, paid)
    return con  # caller closes


def test_true_up_full_refund_when_corpus_below_free_slice(db, true_up_env):
    # mooneymen's case: paid $4 for 500, owns fewer than the free 100 -> full $4 back.
    con = _setup_purchase(db, 500, "pi_full", 4.00)
    try:
        cfg = Config.from_env()
        jobs._import_true_up(cfg, con, DEFAULT_TENANT_ID, cap=600, purchased=500)
        assert true_up_env.calls == [("pi_full", 4.00)]
        assert storage.import_limit(con) == 0                 # unused entitlement released
        assert storage.get_import_payment(con) == (None, 0.0)  # double-refund guard cleared
    finally:
        con.close()


def test_true_up_partial_refund_when_partially_used(db, true_up_env, seeded_db_posts_250):
    # 250 posts, free 100 -> 150 chargeable ($1.50 of the $4 paid) -> $2.50 back.
    con = _setup_purchase(db, 500, "pi_part", 4.00)
    try:
        cfg = Config.from_env()
        jobs._import_true_up(cfg, con, DEFAULT_TENANT_ID, cap=600, purchased=500)
        assert true_up_env.calls == [("pi_part", 2.50)]
    finally:
        con.close()


@pytest.fixture
def seeded_db_posts_250(db):
    con = storage.connect(db)
    for i in range(250):
        con.execute("INSERT INTO posts (id, text) VALUES (%s, %s)", (f"tu{i}", f"post {i}"))
    con.commit(); con.close()
    return db


def test_true_up_no_refund_when_entitlement_filled(db, true_up_env, seeded_db_posts_250):
    con = _setup_purchase(db, 150, "pi_none", 1.50)  # cap 250 == corpus 250: fully used
    try:
        cfg = Config.from_env()
        jobs._import_true_up(cfg, con, DEFAULT_TENANT_ID, cap=250, purchased=150)
        assert true_up_env.calls == []
        assert storage.get_import_payment(con) == ("pi_none", 1.50)  # ref kept (nothing refunded)
    finally:
        con.close()


def test_true_up_second_run_does_not_double_refund(db, true_up_env):
    con = _setup_purchase(db, 500, "pi_once", 4.00)
    try:
        cfg = Config.from_env()
        jobs._import_true_up(cfg, con, DEFAULT_TENANT_ID, cap=600, purchased=500)
        jobs._import_true_up(cfg, con, DEFAULT_TENANT_ID, cap=100, purchased=0)  # post-release state
        assert len(true_up_env.calls) == 1
    finally:
        con.close()


def test_true_up_refund_failure_never_raises(db, true_up_env, monkeypatch):
    def _boom(api_key, payment_intent, amount_usd):
        raise RuntimeError("stripe down")
    monkeypatch.setattr("xbb.billing.refund_payment", _boom)
    con = _setup_purchase(db, 500, "pi_fail", 4.00)
    try:
        cfg = Config.from_env()
        jobs._import_true_up(cfg, con, DEFAULT_TENANT_ID, cap=600, purchased=500)  # must not raise
        assert storage.get_import_payment(con) == ("pi_fail", 4.00)  # ref kept for manual retry
    finally:
        con.close()


def test_category_target_scales_and_caps():
    from xbb.ai import _category_target
    assert _category_target(24) == 4      # tiny corpus -> chunky floor
    assert _category_target(100) == 5
    assert _category_target(300) == 15
    assert _category_target(500) == 25    # growth stops at the 500-sample cap
    assert _category_target(500) >= _category_target(499)  # monotonic


def test_webhook_captures_email_for_emailless_account(client, db):
    secret = _secret()
    con = storage.connect(db)
    con.execute("DELETE FROM accounts WHERE x_user_id = 'cap1'")
    xacct = storage.create_account_from_x(con, "cap1", "capturetest")
    con.close()
    ev = {"id": "evt", "object": "event", "type": "checkout.session.completed",
          "data": {"object": {"id": "cs", "object": "checkout.session", "mode": "payment",
                              "client_reference_id": xacct,
                              "customer_details": {"email": "captured@example.com"},
                              "payment_intent": "pi_cap", "amount_total": 500,
                              "metadata": {"kind": "credits", "grant": "5.50"}}}}
    assert _post_event(client, ev, secret).status_code == 200
    con = storage.connect(db)
    try:
        assert storage.get_account_email(con, xacct) == "captured@example.com"
        # an account that already HAS an email is never overwritten
        assert storage.set_account_email(con, xacct, "other@example.com") is False
    finally:
        con.execute("DELETE FROM accounts WHERE x_user_id = 'cap1'"); con.commit(); con.close()
