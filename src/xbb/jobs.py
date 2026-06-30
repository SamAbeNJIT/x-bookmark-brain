"""Background refill job so the local UI can sync new bookmarks with one button.

Runs backfill (incremental) → index → categorize in a daemon thread, exposing a small
status dict the UI polls. Single-user, single-process: one job at a time, guarded by a lock.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any

from .config import Config

_lock = threading.Lock()
_status: dict[str, Any] = {
    "running": False,
    "step": "idle",          # idle | backfill | index | categorize | done | error
    "detail": "",
    "added": 0,              # new bookmarks pulled this run
    "error": None,
    "started_at": None,
    "finished_at": None,
}


def status() -> dict[str, Any]:
    with _lock:
        return dict(_status)


def _set(**kw: Any) -> None:
    with _lock:
        _status.update(kw)


def _run(cfg: Config, tenant_id: str) -> None:
    from . import categorize, storage, usage, xapi
    from .ai import BedrockAIClient
    from .search import index_posts
    from .storage import connect, init_db

    con = None
    try:
        init_db(cfg.database_url, tenant_id)            # owner: ensure schema (idempotent)
        con = connect(cfg.app_database_url, tenant_id)  # restricted role: RLS-scoped to this tenant

        _set(step="backfill", detail="fetching new bookmarks from X…")
        added = xapi.backfill_via_api(con, cfg.x_client_id, incremental=True)
        _set(added=added)

        ai = BedrockAIClient(
            region=cfg.aws_region,
            embedding_model=cfg.bedrock_embedding_model,
            labeling_model=cfg.bedrock_labeling_model,
            reasoning_model=cfg.bedrock_reasoning_model,
        )

        _set(step="index", detail="embedding new posts…")
        index_posts(con, ai, progress=lambda d, t: _set(detail=f"embedding {d}/{t}"))

        _set(step="categorize", detail="labeling new posts…")
        if not categorize.get_taxonomy(con):
            categorize.save_taxonomy(con, categorize.derive_taxonomy(con, ai))
        categorize.apply_default_parents(con)
        categorize.assign_unassigned(con, ai, progress=lambda d, t: _set(detail=f"labeling {d}/{t}"))

        for e in ai.pop_usage():  # meter the sync's embedding + labeling spend
            storage.record_usage(con, e["model"], e["input_tokens"], e["output_tokens"],
                                 usage.cost_of(e["model"], e["input_tokens"], e["output_tokens"]))

        _set(step="done", detail=f"up to date — {added} new bookmark(s) added")
    except Exception as e:  # surface any failure to the UI instead of dying silently
        _set(step="error", error=f"{type(e).__name__}: {e}")
    finally:
        if con is not None:
            con.close()
        with _lock:
            _status["running"] = False
            _status["finished_at"] = time.time()


def start(tenant_id: str | None = None) -> bool:
    """Kick off a refill for a tenant (defaults to the config tenant). Returns True if it started."""
    with _lock:
        if _status["running"]:
            return False
        _status.update(
            {"running": True, "step": "starting", "detail": "", "added": 0,
             "error": None, "started_at": time.time(), "finished_at": None}
        )
    cfg = Config.from_env()
    tid = tenant_id or cfg.tenant_id
    if not cfg.x_client_id:
        _set(running=False, step="error",
             error="X_CLIENT_ID is not set in .env.", finished_at=time.time())
        return False
    from . import xapi
    from .storage import connect, init_db
    init_db(cfg.database_url, tid)
    _c = connect(cfg.app_database_url, tid)
    try:
        connected = xapi.is_connected(_c)
    finally:
        _c.close()
    if not connected:
        _set(running=False, step="error",
             error="Not connected to X yet — click 'Connect X' on the home page first.",
             finished_at=time.time())
        return False
    threading.Thread(target=_run, args=(cfg, tid), daemon=True).start()
    return True
