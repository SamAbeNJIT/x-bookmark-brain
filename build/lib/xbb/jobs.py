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
    from .storage import connect

    con = None
    try:
        # NB: schema is provisioned at deploy/migration time — NEVER run init_db (table-locking
        # DDL) in the request/sync path; it deadlocks against the request's own open transaction.
        con = connect(cfg.app_database_url, tenant_id)  # restricted role: RLS-scoped to this tenant

        _set(step="backfill", detail="fetching new bookmarks from X…")
        # Entitlement cap: free slice + purchased import_limit (None = unlimited/comped).
        cap = storage.effective_import_cap(con, cfg.free_bookmark_limit)
        n_posts = con.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
        purchased = storage.import_limit(con)
        # Page the FULL timeline (non-incremental) when there's unfetched entitlement below the
        # already-stored newest page — incremental would stop there and never reach older posts:
        #  - purchased entitlement not yet filled (just bought more via the slider), or
        #  - legacy comped/unlimited account still sitting at a partial (free-slice-sized) corpus.
        if cap is None:
            full_import = 0 < n_posts <= cfg.free_bookmark_limit
        else:
            full_import = purchased > 0 and 0 < n_posts < cap
        added = xapi.backfill_via_api(con, cfg.x_client_id,
                                      incremental=not full_import, max_total=cap)
        _set(added=added)

        # Unused purchased capacity → question credits (the slider promise): if the timeline was
        # exhausted below the cap, the surplus entitlement converts at the per-bookmark rate.
        if cap is not None and purchased > 0:
            total_now = con.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
            if total_now < cap:  # backfill stops at cap or exhaustion; below cap = exhausted
                unused = min(cap - total_now, purchased)
                if unused > 0:
                    from . import pricing
                    value = pricing.unused_import_to_credits_usd(unused, cfg.price_per_bookmark_usd)
                    storage.reduce_import_limit(con, unused)
                    row = con.execute("SELECT current_setting('app.current_tenant', true)").fetchone()
                    storage.add_credits(con, row[0], value)
                    _set(detail=f"imported everything — ${value:.2f} of unused import converted to credits")

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
    from .storage import connect
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
