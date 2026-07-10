"""Pricing math for imports and credits — pure, no Stripe, no DB, fully unit-testable.

The import slider sells an ENTITLEMENT of "up to N most-recent bookmarks" without knowing the
user's true count (counting via the X API would cost ~$0.005/bookmark for every curious signup).
If their corpus turns out smaller than N, the unused capacity converts to ask-credits at the
same per-bookmark rate (see jobs) — so a dollar paid is never lost, just re-denominated.
"""

from __future__ import annotations

# Credit top-ups (2026-07-10 pivot: pay-per-question ONLY — no subscription). Base rate is a
# flat 5¢/question; bigger packs grant BONUS credits instead of a lower unit price, so the
# effective per-question price never dips below serving cost (~3.3¢ avg, ~5.3¢ max today).
MIN_CREDIT_TOPUP_USD = 5.00    # below this, Stripe's 30¢+2.9% fee eats the margin
MAX_CREDIT_TOPUP_USD = 100.00
CREDIT_PACK_BONUS = (          # (minimum amount, bonus fraction) — checked top down
    (20.0, 0.30),              # $20+ -> +30% bonus questions (effective ~3.8¢)
    (10.0, 0.20),              # $10+ -> +20% (~4.2¢)
    (5.0, 0.10),               # $5+  -> +10% (~4.5¢)
)

# Legacy subscription constants: removed from sale 2026-07-10 (zero subscribers at the time).
# The webhook grant path stays so any straggler invoice would still be honored.
SUB_PRICE_USD = 3.99
SUB_MONTHLY_CREDITS_USD = 7.50

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
    """Credits granted for a top-up: dollars paid plus the pack bonus tier."""
    for floor, bonus in CREDIT_PACK_BONUS:
        if amount_usd >= floor:
            return round(amount_usd * (1 + bonus), 2)
    return round(amount_usd, 2)
