"""One-time, house-funded grounded answer after the first eligible source enrichment.

State lives in the existing tenant-scoped ``sync_state`` table. The pending insert is the
multi-instance claim: an eligible tenant gets one attempt across all sources and later syncs.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Literal

from . import ask as ask_module
from . import storage
from .ai import AIClient

MIN_POSTS = 5
MIN_CATEGORY_POSTS = 3
AUTO_ANSWER_K = 8
PENDING_TTL_S = 45
STATE_KEY = "auto_answer:v1"
SHOWN_KEY = "auto_answer_shown"

SkipReason = Literal["empty_library", "tiny_library", "no_categories", "no_dominant_category"]


@dataclass(frozen=True)
class Eligibility:
    question: str | None
    reason: SkipReason | None


def pick_question(rows: list[tuple[Any, ...]]) -> str | None:
    """Choose the largest child category, with deterministic input ordering for ties."""
    if not rows:
        return None
    name, count = rows[0][0], int(rows[0][1])
    if not isinstance(name, str) or not name.strip() or count < MIN_CATEGORY_POSTS:
        return None
    return f"What did I save about {name.strip()}?"


def eligible(con) -> Eligibility:
    total = storage.post_count(con)
    if total == 0:
        return Eligibility(None, "empty_library")
    if total < MIN_POSTS:
        return Eligibility(None, "tiny_library")
    rows = con.execute(
        """
        SELECT c.name, COUNT(DISTINCT a.post_id) AS n, c.id
        FROM categories c
        JOIN assignments a ON a.tenant_id = c.tenant_id AND a.category_id = c.id
        WHERE c.parent IS NOT NULL AND btrim(c.parent) <> ''
        GROUP BY c.id, c.name
        ORDER BY n DESC, c.id ASC
        """
    ).fetchall()
    if not rows:
        return Eligibility(None, "no_categories")
    question = pick_question(rows)
    if question is None:
        return Eligibility(None, "no_dominant_category")
    return Eligibility(question, None)


def _dump(state: dict[str, Any]) -> str:
    return json.dumps(state, separators=(",", ":"))


def claim(con, question: str, now: float | None = None) -> bool:
    state = {"v": 1, "status": "pending", "q": question,
             "created_at": float(time.time() if now is None else now)}
    return storage.claim_state(con, STATE_KEY, _dump(state))


def load(con) -> dict[str, Any] | None:
    raw = storage.get_state(con, STATE_KEY)
    if not raw:
        return None
    try:
        state = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(state, dict) or state.get("v") != 1:
        return None
    status = state.get("status")
    if status not in {"pending", "ready", "failed"}:
        return None
    if not isinstance(state.get("created_at"), (int, float)):
        return None
    if status in {"pending", "ready"} and not isinstance(state.get("q"), str):
        return None
    if status == "ready":
        if not isinstance(state.get("answer"), str):
            return None
        if not isinstance(state.get("retrieved_ids"), list) or not isinstance(
                state.get("citations"), list):
            return None
        ids = [str(i) for i in state["retrieved_ids"] if isinstance(i, (str, int))]
        ids = ids[:AUTO_ANSWER_K]
        known = set(ids)
        citations = [str(i) for i in state["citations"] if str(i) in known]
        state = {**state, "retrieved_ids": ids, "citations": citations}
    return state


def is_pending_fresh(state: dict[str, Any] | None, now: float | None = None) -> bool:
    return bool(
        state
        and state.get("status") == "pending"
        and float(time.time() if now is None else now) - float(state["created_at"])
        < PENDING_TTL_S
    )


def rewrite_citation_ids(answer: str | None, retrieved_ids: list[str],
                         citations: list[str]) -> tuple[str, dict[str, int]]:
    """Replace raw retrieved ids in model prose with stable numbered citation markers."""
    text = answer or ""
    refno = {str(post_id): i + 1 for i, post_id in enumerate(citations)}
    for post_id in retrieved_ids:
        post_id = str(post_id)
        if post_id and post_id in text:
            number = refno.setdefault(post_id, len(refno) + 1)
            text = text.replace(f"({post_id})", f"[{number}]").replace(post_id, f"[{number}]")
    return text, refno


def generate(con, ai: AIClient, question: str) -> dict[str, Any]:
    """Run the normal grounded RAG path directly, without any Ask billing/counter mutation."""
    result = ask_module.ask(con, ai, question, k=AUTO_ANSWER_K)
    retrieved_ids = [str(p["id"]) for p in result.get("retrieved", [])][:AUTO_ANSWER_K]
    known = set(retrieved_ids)
    citations = [str(c) for c in result.get("citations", []) if str(c) in known]
    answer, _ = rewrite_citation_ids(result.get("answer"), retrieved_ids, citations)
    state = {
        "v": 1,
        "status": "ready",
        "q": question,
        "answer": answer,
        "citations": citations,
        "retrieved_ids": retrieved_ids,
        "created_at": time.time(),
    }
    storage.set_state(con, STATE_KEY, _dump(state))
    return state


def save_failed(con, now: float | None = None) -> None:
    storage.set_state(con, STATE_KEY, _dump({
        "v": 1,
        "status": "failed",
        "created_at": float(time.time() if now is None else now),
    }))
