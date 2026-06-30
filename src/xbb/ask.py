"""Ask mode / RAG (issue #7): retrieve relevant bookmarks, synthesize a cited answer.

Citations are constrained to the retrieved set — the answer can only cite posts that were
actually pulled, never fabricated sources.
"""

from __future__ import annotations

from typing import Any

import psycopg

from .ai import AIClient
from .search import search


def ask(con: psycopg.Connection, ai: AIClient, question: str, k: int = 8) -> dict[str, Any]:
    retrieved = search(con, ai, question, k)
    result = ai.answer(question, retrieved)
    retrieved_ids = {r["id"] for r in retrieved}
    citations = [c for c in result.get("citations", []) if c in retrieved_ids]
    return {
        "question": question,
        "answer": result.get("answer"),
        "citations": citations,
        "retrieved": retrieved,
    }
