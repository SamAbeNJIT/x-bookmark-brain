"""Stripe billing wrapper — pure, stateless, no env reads, no DB.

Thin functions over the official ``stripe`` SDK. The caller always passes the secrets
(``api_key`` / ``webhook_secret``) in as arguments, so nothing here touches the environment
and every function is trivial to mock in a unit test.

Three responsibilities, deliberately split so the app wiring owns the policy:

  - :func:`create_checkout_session` — start a subscription Checkout, return the redirect URL.
  - :func:`construct_event` — verify a webhook signature and parse it into a Stripe event.
  - :func:`summarize_event` — extract the fields the app cares about from a handled event.

``summarize_event`` makes **no** business decisions (active vs. canceled, grace periods, etc.).
It only pulls fields out of the event; the caller interprets ``status``.
"""

from __future__ import annotations

import stripe

# Webhook event types we know how to summarize. Anything else → summarize_event returns None.
_HANDLED_EVENTS = frozenset(
    {
        "checkout.session.completed",
        "customer.subscription.updated",
        "customer.subscription.deleted",
    }
)


def create_checkout_session(
    api_key: str,
    price_id: str,
    customer_email: str,
    client_reference_id: str,
    success_url: str,
    cancel_url: str,
) -> str:
    """Create a subscription Checkout Session and return its hosted ``url``.

    ``client_reference_id`` is echoed back on the resulting ``checkout.session.completed``
    webhook, which is how the app links the Stripe customer to its own account. The signing
    secret is set on the SDK locally (never read from the environment).
    """
    stripe.api_key = api_key
    session = stripe.checkout.Session.create(
        mode="subscription",
        line_items=[{"price": price_id, "quantity": 1}],
        customer_email=customer_email,
        client_reference_id=client_reference_id,
        success_url=success_url,
        cancel_url=cancel_url,
    )
    return session.url


def create_payment_session(
    api_key: str,
    price_id: str,
    customer_email: str | None,
    client_reference_id: str,
    success_url: str,
    cancel_url: str,
    metadata: dict[str, str],
) -> str:
    """Create a one-time (``mode="payment"``) Checkout Session and return its hosted ``url``.

    Used for the prepaid model: a one-off ingestion charge or a credit-pack purchase. ``metadata``
    (e.g. ``{"kind": "ingestion"}`` or ``{"kind": "credits"}``) is echoed on the completed-session
    webhook so the app knows what was bought; ``client_reference_id`` links it to the account.
    ``customer_email=None`` lets Stripe collect the buyer's real address.
    """
    stripe.api_key = api_key
    kwargs: dict = {}
    if customer_email:
        kwargs["customer_email"] = customer_email
    session = stripe.checkout.Session.create(
        mode="payment",
        line_items=[{"price": price_id, "quantity": 1}],
        client_reference_id=client_reference_id,
        success_url=success_url,
        cancel_url=cancel_url,
        metadata=metadata,
        **kwargs,
    )
    return session.url


def create_subscription_session(
    api_key: str,
    price_id: str,
    customer_email: str,
    client_reference_id: str,
    success_url: str,
    cancel_url: str,
    subscription_metadata: dict[str, str],
) -> str:
    """Subscription Checkout whose metadata lands ON THE SUBSCRIPTION (not just the session).

    Stripe denormalizes subscription metadata onto every invoice (``subscription_details.
    metadata``), so each month's ``invoice.paid`` webhook self-identifies the account and
    purpose — no cross-event ordering or extra API lookups needed for renewal grants.
    """
    stripe.api_key = api_key
    session = stripe.checkout.Session.create(
        mode="subscription",
        line_items=[{"price": price_id, "quantity": 1}],
        customer_email=customer_email,
        client_reference_id=client_reference_id,
        success_url=success_url,
        cancel_url=cancel_url,
        subscription_data={"metadata": subscription_metadata},
    )
    return session.url


def create_amount_session(
    api_key: str,
    amount_usd: float,
    product_name: str,
    customer_email: str | None,
    client_reference_id: str,
    success_url: str,
    cancel_url: str,
    metadata: dict[str, str],
) -> str:
    """One-time Checkout for a DYNAMIC amount (no pre-created price) via ``price_data``.

    Used for the import slider (price computed from the chosen bookmark count) and custom
    credit top-ups. Same webhook contract as ``create_payment_session``.

    ``customer_email=None`` (e.g. an X-sign-in account with no email yet) omits the prefill so
    Stripe collects the buyer's real address — the webhook then saves it onto the account.
    """
    stripe.api_key = api_key
    kwargs: dict = {}
    if customer_email:
        kwargs["customer_email"] = customer_email
    session = stripe.checkout.Session.create(
        mode="payment",
        line_items=[{
            "price_data": {
                "currency": "usd",
                "unit_amount": int(round(amount_usd * 100)),
                "product_data": {"name": product_name},
            },
            "quantity": 1,
        }],
        client_reference_id=client_reference_id,
        success_url=success_url,
        cancel_url=cancel_url,
        metadata=metadata,
        **kwargs,
    )
    return session.url


def refund_payment(api_key: str, payment_intent: str, amount_usd: float):
    """Partially (or fully) refund a payment — the import true-up: users only pay for the
    bookmarks they actually had. Caller decides the amount; this just moves the money."""
    stripe.api_key = api_key
    return stripe.Refund.create(payment_intent=payment_intent,
                                amount=int(round(amount_usd * 100)))


def construct_event(payload: bytes, sig_header: str, webhook_secret: str):
    """Verify a webhook signature and return the parsed Stripe event.

    Delegates to ``stripe.Webhook.construct_event``; lets it raise on a bad/missing signature
    (``stripe.error.SignatureVerificationError``) or malformed payload (``ValueError``) so the
    caller can reject the request.
    """
    return stripe.Webhook.construct_event(payload, sig_header, webhook_secret)


def summarize_event(event) -> dict | None:
    """Flatten a handled subscription event into the fields the app wiring needs.

    Returns ``None`` for any event type we don't handle. For handled types, returns a dict with
    ``type``, ``customer_id``, ``subscription_id``, ``status``, ``client_reference_id`` and
    ``customer_email`` — every value pulled defensively with ``.get`` (any may be ``None``).

    Note the two object shapes: a ``checkout.session.completed`` object is a Checkout Session
    (its subscription id lives under ``subscription``), whereas the ``customer.subscription.*``
    object *is* the Subscription (its id is ``id``).
    """
    event_type = event.get("type")
    if event_type not in _HANDLED_EVENTS:
        return None

    obj = event.get("data", {}).get("object", {})

    if event_type == "checkout.session.completed":
        subscription_id = obj.get("subscription")
    else:  # customer.subscription.updated / .deleted — the object is the Subscription itself
        subscription_id = obj.get("id")

    # Checkout Sessions carry the email at top level; some payloads nest it under customer_details.
    customer_email = obj.get("customer_email")
    if customer_email is None:
        customer_email = obj.get("customer_details", {}).get("email")

    return {
        "type": event_type,
        "customer_id": obj.get("customer"),
        "subscription_id": subscription_id,
        "status": obj.get("status"),
        "client_reference_id": obj.get("client_reference_id"),
        "customer_email": customer_email,
    }
