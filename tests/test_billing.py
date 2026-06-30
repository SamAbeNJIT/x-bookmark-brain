"""Pure unit tests for billing.py — no network, no DB, no DATABASE_URL, no Stripe key.

The ``stripe`` module is monkeypatched with fakes so we assert *what we send to Stripe* and
*how we map what Stripe sends back*, without any HTTP. Nothing here touches storage or the app.
"""

import pytest

from xbb import billing


# --- fakes -------------------------------------------------------------------------------


class _FakeSession:
    """Stand-in for a created Checkout Session — only needs a .url."""

    url = "https://checkout.stripe.test/c/session_abc123"


@pytest.fixture
def fake_stripe(monkeypatch):
    """Replace billing.stripe with a recording fake and return it."""

    captured = {}

    class FakeSessionNS:
        @staticmethod
        def create(**kwargs):
            captured["create_kwargs"] = kwargs
            return _FakeSession()

    class FakeCheckout:
        Session = FakeSessionNS

    class FakeWebhook:
        @staticmethod
        def construct_event(payload, sig_header, webhook_secret):
            captured["construct_args"] = (payload, sig_header, webhook_secret)
            return {"verified": True, "payload": payload}

    class FakeStripe:
        api_key = None
        checkout = FakeCheckout
        Webhook = FakeWebhook

    fake = FakeStripe()
    monkeypatch.setattr(billing, "stripe", fake)
    fake._captured = captured
    return fake


# --- create_checkout_session -------------------------------------------------------------


def test_create_checkout_session_sets_key_and_passes_args(fake_stripe):
    url = billing.create_checkout_session(
        api_key="sk_test_xyz",
        price_id="price_123",
        customer_email="alice@example.com",
        client_reference_id="acct_42",
        success_url="https://app.test/ok",
        cancel_url="https://app.test/no",
    )

    # api_key is set on the SDK locally (not read from env)
    assert fake_stripe.api_key == "sk_test_xyz"

    kwargs = fake_stripe._captured["create_kwargs"]
    assert kwargs["mode"] == "subscription"
    assert kwargs["line_items"] == [{"price": "price_123", "quantity": 1}]
    assert kwargs["customer_email"] == "alice@example.com"
    assert kwargs["client_reference_id"] == "acct_42"
    assert kwargs["success_url"] == "https://app.test/ok"
    assert kwargs["cancel_url"] == "https://app.test/no"

    # returns the hosted session URL
    assert url == "https://checkout.stripe.test/c/session_abc123"


# --- construct_event ---------------------------------------------------------------------


def test_construct_event_delegates_to_stripe_webhook(fake_stripe):
    out = billing.construct_event(b"raw-bytes", "t=1,v1=sig", "whsec_test")

    assert fake_stripe._captured["construct_args"] == (b"raw-bytes", "t=1,v1=sig", "whsec_test")
    assert out == {"verified": True, "payload": b"raw-bytes"}


def test_construct_event_propagates_signature_error(monkeypatch):
    class Boom(Exception):
        pass

    class FakeWebhook:
        @staticmethod
        def construct_event(payload, sig_header, webhook_secret):
            raise Boom("bad signature")

    class FakeStripe:
        Webhook = FakeWebhook

    monkeypatch.setattr(billing, "stripe", FakeStripe())

    with pytest.raises(Boom):
        billing.construct_event(b"x", "bad", "whsec_test")


# --- summarize_event ---------------------------------------------------------------------


def test_summarize_checkout_session_completed():
    event = {
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "customer": "cus_1",
                "subscription": "sub_1",
                "status": "complete",
                "client_reference_id": "acct_42",
                "customer_email": "alice@example.com",
            }
        },
    }
    assert billing.summarize_event(event) == {
        "type": "checkout.session.completed",
        "customer_id": "cus_1",
        "subscription_id": "sub_1",
        "status": "complete",
        "client_reference_id": "acct_42",
        "customer_email": "alice@example.com",
    }


def test_summarize_checkout_session_falls_back_to_customer_details_email():
    event = {
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "customer": "cus_1",
                "subscription": "sub_1",
                "status": "complete",
                "client_reference_id": "acct_42",
                "customer_details": {"email": "nested@example.com"},
            }
        },
    }
    assert billing.summarize_event(event)["customer_email"] == "nested@example.com"


def test_summarize_subscription_updated_uses_object_id_as_subscription_id():
    event = {
        "type": "customer.subscription.updated",
        "data": {
            "object": {
                "id": "sub_99",
                "customer": "cus_7",
                "status": "active",
            }
        },
    }
    assert billing.summarize_event(event) == {
        "type": "customer.subscription.updated",
        "customer_id": "cus_7",
        "subscription_id": "sub_99",
        "status": "active",
        "client_reference_id": None,
        "customer_email": None,
    }


def test_summarize_subscription_deleted():
    event = {
        "type": "customer.subscription.deleted",
        "data": {
            "object": {
                "id": "sub_99",
                "customer": "cus_7",
                "status": "canceled",
            }
        },
    }
    summary = billing.summarize_event(event)
    assert summary["type"] == "customer.subscription.deleted"
    assert summary["subscription_id"] == "sub_99"
    assert summary["status"] == "canceled"


def test_summarize_unhandled_event_returns_none():
    event = {"type": "invoice.paid", "data": {"object": {"id": "in_1"}}}
    assert billing.summarize_event(event) is None
