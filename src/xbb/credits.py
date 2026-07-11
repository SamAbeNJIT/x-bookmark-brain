"""Credit-gated ask: free daily allowance first, then the prepaid balance, shared by both routes.

Both `/ask` (JSON) and `/ui/ask` (HTML) go through `ask_charged` so the gate is enforced once,
consistently. Order: consume one of today's free asks (freemium: N/day) if any remain; otherwise
charge the prepaid balance. Charges are taken up front and atomically (so a balance can't go
negative under concurrent asks) and refunded — free-ask or credit — if the underlying ask errors.
"""

from __future__ import annotations

from typing import Any

import psycopg

from . import storage
from .log import logger
from .ai import AIClient
from .ask import ask


class OutOfCredits(Exception):
    """Raised when today's free asks are used up and the balance can't cover one ask."""


def _refund_free_ask(con: psycopg.Connection) -> None:
    con.execute(
        "UPDATE sync_state SET value = GREATEST(value::int - 1, 0)::text "
        "WHERE key = 'free_asks:' || to_char(now(), 'YYYY-MM-DD')"
    )
    con.commit()


def ask_charged(
    con: psycopg.Connection,
    ai: AIClient,
    question: str,
    k: int,
    ask_price_usd: float,
    free_asks_per_day: int = 0,
    history: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    used_free = free_asks_per_day > 0 and storage.use_free_ask(con, free_asks_per_day)
    logger.info("ask.request price=%.2f turns=%d", ask_price_usd, len(history or []))
    if not used_free and not storage.debit_credits(con, ask_price_usd):
        raise OutOfCredits()
    try:
        return ask(con, ai, question, k, history=history)
    except Exception:
        # Don't charge for a failed answer — return whichever allowance was consumed.
        if used_free:
            _refund_free_ask(con)
        else:
            storage.refund_credits(con, ask_price_usd)
        raise
