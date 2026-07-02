"""Pricing math for imports and credits — pure, no Stripe, no DB, fully unit-testable.

The import slider sells an ENTITLEMENT of "up to N most-recent bookmarks" without knowing the
user's true count (counting via the X API would cost ~$0.005/bookmark for every curious signup).
If their corpus turns out smaller than N, the unused capacity converts to ask-credits at the
same per-bookmark rate (see jobs) — so a dollar paid is never lost, just re-denominated.
"""

from __future__ import annotations

# One-time credit top-ups convert 1:1 (a dollar buys a dollar of asks).
MIN_CREDIT_TOPUP_USD = 5.00    # below this, Stripe's 30¢+2.9% fee eats the margin
MAX_CREDIT_TOPUP_USD = 100.00

# Monthly credit subscription: cheaper than buying one-time ("more bang for your buck").
SUB_PRICE_USD = 3.99
SUB_MONTHLY_CREDITS_USD = 5.00

IMPORT_SLIDER_MIN = 500        # keeps every charge comfortably above Stripe's fee floor
IMPORT_SLIDER_MAX = 20_000
IMPORT_SLIDER_STEP = 100


def import_price_usd(n: int, free_limit: int, per_bookmark_usd: float) -> float:
    """Price for an entitlement of `n` total bookmarks; the free slice is deducted."""
    return round(max(0, n - free_limit) * per_bookmark_usd, 2)


def unused_import_to_credits_usd(unused_bookmarks: int, per_bookmark_usd: float) -> float:
    """Dollar value of unused import capacity, converted to ask-credits (same rate as paid)."""
    return round(max(0, unused_bookmarks) * per_bookmark_usd, 2)


def credits_for_topup(amount_usd: float) -> float:
    """One-time top-up: 1:1 dollars → credits."""
    return round(amount_usd, 2)
