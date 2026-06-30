"""Credit-gated ask: charge the prepaid balance per question, shared by the JSON + HTML routes.

Both `/ask` (JSON) and `/ui/ask` (HTML) go through `ask_charged` so the credit gate is enforced
once, consistently. The charge is taken up front and atomically (so it can't go negative under
concurrent asks) and refunded if the underlying ask errors.
"""

from __future__ import annotations

from typing import Any

import psycopg

from . import storage
from .ai import AIClient
from .ask import ask


class OutOfCredits(Exception):
    """Raised when the tenant's balance can't cover one ask."""


def ask_charged(
    con: psycopg.Connection, ai: AIClient, question: str, k: int, ask_price_usd: float
) -> dict[str, Any]:
    if not storage.debit_credits(con, ask_price_usd):
        raise OutOfCredits()
    try:
        return ask(con, ai, question, k)
    except Exception:
        storage.refund_credits(con, ask_price_usd)  # don't charge for a failed answer
        raise
