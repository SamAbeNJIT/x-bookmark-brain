"""Pricing math for imports and credits — pure, no Stripe, no DB, fully unit-testable.

IMPORTS (2026-07-13 pivot, third billing model): buyers purchase a dollar amount ($5-$200) of
prepaid "imports" — 1 import brings 1 saved item into the library (bookmarks today; the unit
is deliberately source-vague for future sources). The balance (accounts.import_limit) is
ADDITIVE on top of the free slice and ROLLS OVER: unused imports cover whatever the user
saves next, so paying customers never re-hit a $-minimum paywall for a handful of new
bookmarks. No auto-refund (the 2026-07-10 true-up model is retired); refunds are on request
via support.
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

# Imports purchase band, in DOLLARS (owner call, 2026-07-13: "$5-$200, make it clear how many
# imports that dollar amount accounts for"). $5 floor keeps Stripe's 30¢+2.9% fee tolerable.
IMPORT_MIN_USD = 3.0    # lowered from $5 (owner, 2026-07-15: cut first-payment friction —
                        # two abandoned checkouts in the first 24h of the $5/$10 band)
IMPORT_MAX_USD = 200.0
IMPORT_STEP_USD = 1.0   # $1 steps so the $3 floor is actually reachable on the slider


def imports_for_usd(amount_usd: float, per_import_usd: float) -> int:
    """How many imports a dollar amount buys (e.g. $10 at 1¢ -> 1,000)."""
    if per_import_usd <= 0:
        return 0
    return int(round(amount_usd / per_import_usd))


def credits_for_topup(amount_usd: float) -> float:
    """Credits granted for a top-up: dollars paid plus the pack bonus tier."""
    for floor, bonus in CREDIT_PACK_BONUS:
        if amount_usd >= floor:
            return round(amount_usd * (1 + bonus), 2)
    return round(amount_usd, 2)
