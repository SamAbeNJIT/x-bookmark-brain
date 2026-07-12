"""Ask mode / RAG (issue #7): retrieve relevant bookmarks, synthesize a cited answer.

Citations are constrained to the retrieved set — the answer can only cite posts that were
actually pulled, never fabricated sources.

Multi-turn: the caller may pass the prior conversation (`history`). The server stays
stateless — the thread lives client-side (a hidden form field on /ui/ask, or the caller's
own state for /ask) and is sent with each request. Follow-ups are rewritten into a
standalone search query before retrieval so "which of those are about evals?" still finds
the right bookmarks.
"""

from __future__ import annotations

from typing import Any

import psycopg

from .ai import AIClient
from .rerank import rerank
from .search import search

# Hybrid search casts a wide net; the cross-encoder keeps the k that actually answer.
# Measured on the owner-corpus eval set: mean precision@30 0.61 -> 0.74.
RERANK_POOL = 100

# Bounds on what a client-supplied thread can make us send to the model: the last N turns,
# each capped in length. Keeps per-turn Bedrock input cost safely under the ask price and
# neuters oversized/hostile payloads (the field is client-editable by design).
HISTORY_MAX_TURNS = 6
HISTORY_MAX_CHARS = 1500


def trim_history(history: Any) -> list[dict[str, str]]:
    """Validate + bound a client-supplied conversation: keep only well-formed user/assistant
    turns, the last HISTORY_MAX_TURNS of them, each capped at HISTORY_MAX_CHARS."""
    if not isinstance(history, list):
        return []
    turns = [
        {"role": t["role"], "content": t["content"][:HISTORY_MAX_CHARS]}
        for t in history
        if isinstance(t, dict)
        and t.get("role") in ("user", "assistant")
        and isinstance(t.get("content"), str)
        and t["content"].strip()
    ]
    return turns[-HISTORY_MAX_TURNS:]


def ask(
    con: psycopg.Connection,
    ai: AIClient,
    question: str,
    k: int = 8,
    history: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    history = trim_history(history)
    query = ai.rewrite_query(question, history) if history else question
    retrieved = rerank(ai, query, search(con, ai, query, max(k, RERANK_POOL)), k)
    result = ai.answer(question, retrieved, history)
    retrieved_ids = {r["id"] for r in retrieved}
    citations = [c for c in result.get("citations", []) if c in retrieved_ids]
    return {
        "question": question,
        "answer": result.get("answer"),
        "citations": citations,
        "retrieved": retrieved,
    }
