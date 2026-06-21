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


def _run(cfg: Config) -> None:
    from . import categorize
    from .ai import BedrockAIClient
    from .ingestion import DEFAULT_QUERY_ID, GraphQLXClient, run_backfill
    from .search import index_posts
    from .storage import connect, init_db

    try:
        init_db(cfg.db_path)
        con = connect(cfg.db_path)
        before = con.execute("SELECT COUNT(*) FROM posts").fetchone()[0]

        _set(step="backfill", detail="fetching new bookmarks from X…")
        client = GraphQLXClient(
            cfg.x_auth_token,
            cfg.x_csrf_token,
            query_id=os.getenv("X_BOOKMARKS_QUERY_ID", DEFAULT_QUERY_ID),
        )
        run_backfill(client, cfg.db_path, incremental=True)
        added = con.execute("SELECT COUNT(*) FROM posts").fetchone()[0] - before
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

        con.close()
        _set(step="done", detail=f"up to date — {added} new bookmark(s) added")
    except Exception as e:  # surface any failure to the UI instead of dying silently
        _set(step="error", error=f"{type(e).__name__}: {e}")
    finally:
        with _lock:
            _status["running"] = False
            _status["finished_at"] = time.time()


def start() -> bool:
    """Kick off a refill if one isn't already running. Returns True if it started."""
    with _lock:
        if _status["running"]:
            return False
        _status.update(
            {"running": True, "step": "starting", "detail": "", "added": 0,
             "error": None, "started_at": time.time(), "finished_at": None}
        )
    cfg = Config.from_env()
    if not cfg.x_auth_token or not cfg.x_csrf_token:
        _set(running=False, step="error",
             error="Missing X_AUTH_TOKEN / X_CSRF_TOKEN in .env (rotate/refresh your X cookies).",
             finished_at=time.time())
        return False
    threading.Thread(target=_run, args=(cfg,), daemon=True).start()
    return True
