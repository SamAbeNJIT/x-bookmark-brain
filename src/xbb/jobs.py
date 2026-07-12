"""Background sync jobs, tracked PER TENANT so concurrent users never block each other.

Each tenant gets an independent status entry and at most one running job; different tenants run
concurrently (each sync: backfill → embed → categorize in its own daemon thread, RLS-scoped to
that tenant via the restricted role). The UI polls `status(tenant_id)` for its own progress.

NB: schema is provisioned at deploy/migration time — NEVER run init_db (table-locking DDL) in
this path; it deadlocks against a request's own open transaction (see playbook war story 9.1).
"""

from __future__ import annotations

import threading
import time
from typing import Any

from .config import Config
from .log import logger

_lock = threading.Lock()
_IDLE: dict[str, Any] = {
    "running": False,
    "step": "idle",          # idle | starting | backfill | index | categorize | done | error
    "detail": "",
    "added": 0,              # new bookmarks pulled this run
    "error": None,
    "started_at": None,
    "finished_at": None,
}
_jobs: dict[str, dict[str, Any]] = {}  # tenant_id -> status (one entry per tenant, tiny)


def status(tenant_id: str) -> dict[str, Any]:
    """This tenant's job status (idle defaults if they've never synced)."""
    with _lock:
        return dict(_jobs.get(tenant_id) or _IDLE)


def _set(tenant_id: str, **kw: Any) -> None:
    with _lock:
        _jobs.setdefault(tenant_id, dict(_IDLE)).update(kw)


def _import_true_up(cfg: Config, con, tenant_id: str, cap: int | None, purchased: int) -> None:
    """Unused purchased capacity → automatic REFUND (2026-07-10 pivot: users only pay for the
    bookmarks they actually have; the old credits-conversion is retired). Runs after backfill:
    if the timeline exhausted below the entitlement, release the unused limit and refund
    everything paid beyond what was actually chargeable. Money hiccups never kill the sync."""
    from . import billing, mail, storage

    if cap is None or purchased <= 0:
        return
    total_now = con.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
    if total_now >= cap:  # entitlement filled exactly — nothing unused
        return
    unused = min(cap - total_now, purchased)
    if unused > 0:
        storage.reduce_import_limit(con, unused)
    pi, paid = storage.get_import_payment(con)
    used_chargeable = max(0, total_now - cfg.free_bookmark_limit) * cfg.price_per_bookmark_usd
    refund = round(min(paid, paid - used_chargeable), 2)
    if refund <= 0 or not pi:
        return
    try:
        billing.refund_payment(cfg.stripe_secret_key, pi, refund)
        storage.set_import_payment(con, tenant_id, None, None)  # double-refund guard
    except Exception as e:
        logger.exception("billing.refund_failed tenant=%s pi=%s amount=%.2f: %s",
                         tenant_id, pi, refund, e)
        mail.send_owner_alert(
            "💸 REFUND FAILED — manual action needed",
            f"tenant {tenant_id}: refund ${refund:.2f} on {pi} failed ({type(e).__name__}). "
            "Issue it from the Stripe dashboard.",
            ses_sender=cfg.ses_sender, owner_email=cfg.owner_alert_email, region=cfg.aws_region)
    else:
        logger.info("billing.refund tenant=%s amount=%.2f", tenant_id, refund)
        mail.send_owner_alert(
            "💸 x-bookmarks auto-refund",
            f"tenant {tenant_id}: imported everything ({total_now} bookmarks); refunded "
            f"${refund:.2f} of unused import capacity.",
            ses_sender=cfg.ses_sender, owner_email=cfg.owner_alert_email, region=cfg.aws_region)
        _set(tenant_id, detail=f"imported everything — ${refund:.2f} refunded to your card")


def _run(cfg: Config, tenant_id: str) -> None:
    from . import categorize, storage, usage, xapi
    from .ai import BedrockAIClient
    from .search import index_posts
    from .storage import connect

    con = None
    logger.info("sync.start tenant=%s", tenant_id)
    try:
        con = connect(cfg.app_database_url, tenant_id)  # restricted role: RLS-scoped tenant

        _set(tenant_id, step="backfill", detail="fetching new bookmarks from X…")
        # Entitlement cap: free slice + purchased import_limit (None = unlimited/comped).
        cap = storage.effective_import_cap(con, cfg.free_bookmark_limit)
        n_posts = con.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
        purchased = storage.import_limit(con)
        # Page the FULL timeline (non-incremental) when there's unfetched entitlement below the
        # already-stored newest page — incremental would stop there and never reach older posts.
        if cap is None:
            full_import = 0 < n_posts <= cfg.free_bookmark_limit
        else:
            full_import = purchased > 0 and 0 < n_posts < cap
        added = xapi.backfill_via_api(con, cfg.x_client_id,
                                      incremental=not full_import, max_total=cap)
        _set(tenant_id, added=added)
        logger.info("sync.backfill tenant=%s added=%d cap=%s", tenant_id, added, cap)

        _import_true_up(cfg, con, tenant_id, cap, purchased)

        ai = BedrockAIClient(
            region=cfg.aws_region,
            embedding_model=cfg.bedrock_embedding_model,
            labeling_model=cfg.bedrock_labeling_model,
            reasoning_model=cfg.bedrock_reasoning_model,
        )

        logger.info("sync.index tenant=%s", tenant_id)
        _set(tenant_id, step="index", detail="embedding new posts…")
        index_posts(con, ai, progress=lambda d, t: _set(tenant_id, detail=f"embedding {d}/{t}"))

        logger.info("sync.categorize tenant=%s", tenant_id)
        _set(tenant_id, step="categorize", detail="labeling new posts…")
        if not categorize.get_taxonomy(con):
            categorize.save_taxonomy(con, categorize.derive_taxonomy(con, ai))
        categorize.apply_default_parents(con)
        categorize.derive_parents(con, ai)  # per-tenant parent themes (no-op if all parented)
        categorize.assign_unassigned(
            con, ai, progress=lambda d, t: _set(tenant_id, detail=f"labeling {d}/{t}"))

        for e in ai.pop_usage():  # meter the sync's embedding + labeling spend
            storage.record_usage(con, e["model"], e["input_tokens"], e["output_tokens"],
                                 usage.cost_of(e["model"], e["input_tokens"], e["output_tokens"]))

        _set(tenant_id, step="done", detail=f"up to date — {added} new bookmark(s) added")
        logger.info("sync.done tenant=%s added=%d", tenant_id, added)
        if storage.is_capped_free(con, cfg.free_bookmark_limit):
            total = con.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
            logger.info("funnel.cap_hit tenant=%s posts=%d", tenant_id, total)
    except Exception as e:  # surface any failure to the UI instead of dying silently
        logger.exception("sync.error tenant=%s: %s", tenant_id, e)  # full traceback -> CloudWatch
        _set(tenant_id, step="error", error=f"{type(e).__name__}: {e}")
    finally:
        if con is not None:
            con.close()
        _set(tenant_id, running=False, finished_at=time.time())


def start(tenant_id: str | None = None) -> bool:
    """Kick off a sync for this tenant if THEIR job isn't already running. Other tenants'
    running jobs never block this one. Returns True if it started."""
    cfg = Config.from_env()
    tid = tenant_id or cfg.tenant_id
    with _lock:
        if _jobs.get(tid, {}).get("running"):
            return False
        _jobs[tid] = dict(_IDLE)
        _jobs[tid].update({"running": True, "step": "starting",
                           "started_at": time.time(), "finished_at": None})
    if not cfg.x_client_id:
        _set(tid, running=False, step="error",
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
        _set(tid, running=False, step="error",
             error="Not connected to X yet — click 'Connect X' on the home page first.",
             finished_at=time.time())
        return False
    threading.Thread(target=_run, args=(cfg, tid), daemon=True).start()
    return True
