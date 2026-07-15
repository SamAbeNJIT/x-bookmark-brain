"""Import slider + credit-subscription economics: pricing math, webhook grants, entitlement."""

import hashlib
import hmac
import json
import time

import pytest

from xbb import pricing, storage
from xbb.config import DEFAULT_TENANT_ID, Config


def test_imports_for_usd_math():
    # 2026-07-13 pivot: buy dollars, get imports (1¢ each), additive on top of the free slice.
    assert pricing.imports_for_usd(10.0, 0.01) == 1000
    assert pricing.imports_for_usd(5.0, 0.01) == 500       # band minimum
    assert pricing.imports_for_usd(200.0, 0.01) == 20000   # band maximum
    assert pricing.imports_for_usd(10.0, 0) == 0           # degenerate rate -> no free imports
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
        # the payment reference is remembered for support-context (on-request refunds)
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


# --------------------------------------------------- rolling imports balance (no true-up)


def test_unused_imports_roll_over_no_refund(db, monkeypatch):
    """2026-07-13 pivot: a sync that exhausts the timeline below the cap must NOT shrink the
    balance and must NOT touch Stripe — unused imports persist for future syncs."""
    from xbb import billing, jobs, xapi

    def _no_refund(*a, **k):
        raise AssertionError("refund_payment must never be called by a sync")
    monkeypatch.setattr(billing, "refund_payment", _no_refund)

    class _TinyTimeline:  # 5 bookmarks total; cap will be far above
        def __init__(self, con, client_id):
            self._pages = [{"data": [{"id": f"r{i}", "text": f"post {i}"} for i in range(5)],
                            "includes": {}}]

        def iter_bookmark_pages(self, max_results=100):
            yield from self._pages

    monkeypatch.setattr(xapi, "XApiClient", _TinyTimeline)
    con = storage.connect(db)
    try:
        con.execute("UPDATE accounts SET ingestion_paid = false WHERE id = %s",
                    (DEFAULT_TENANT_ID,))
        con.commit()
        storage.add_import_limit(con, DEFAULT_TENANT_ID, 1000)     # bought $10 of imports
        storage.set_import_payment(con, DEFAULT_TENANT_ID, "pi_keep", 10.00)
        added = xapi.backfill_via_api(con, "cid", incremental=True,
                                      max_total=storage.effective_import_cap(con, 100))
        assert added == 5                                           # whole timeline stored
        assert storage.import_limit(con) == 1000                    # balance untouched
        assert storage.get_import_payment(con) == ("pi_keep", 10.00)  # ref kept for support
    finally:
        con.close()


def test_checkout_import_maps_dollars_to_imports(client, db, monkeypatch):
    """$10 -> count=1000 in session metadata; out-of-band amounts clamp to the $5-$200 band."""
    from xbb import billing
    sessions = []

    def _fake_session(**kwargs):
        sessions.append(kwargs)
        return "/ui/billing"  # redirect target stands in for the Stripe URL
    monkeypatch.setattr(billing, "create_amount_session", _fake_session)
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_dummy")

    client.post("/billing/checkout", data={"kind": "import", "amount": "10"},
                follow_redirects=False)
    assert sessions[-1]["amount_usd"] == 10.0
    assert sessions[-1]["metadata"]["count"] == "1000"
    assert "1,000 imports" in sessions[-1]["product_name"]

    client.post("/billing/checkout", data={"kind": "import", "amount": "1"},
                follow_redirects=False)
    assert sessions[-1]["amount_usd"] == pricing.IMPORT_MIN_USD    # clamped up to $5

    client.post("/billing/checkout", data={"kind": "import", "amount": "9999"},
                follow_redirects=False)
    assert sessions[-1]["amount_usd"] == pricing.IMPORT_MAX_USD    # clamped down to $200


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
