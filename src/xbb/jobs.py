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

import httpx

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


# NOTE: the automatic import true-up/refund (2026-07-10..13) is RETIRED — unused imports now
# ROLL OVER as a prepaid balance (2026-07-13 pivot; see pricing.py docstring). Refunds are
# on-request via support: billing.refund_payment + the payment ref stored on accounts.


def _embed_and_label(cfg: Config, con, tenant_id: str) -> None:
    """The shared pipeline tail: embed whatever's unindexed, then label whatever's unlabeled.
    Source-agnostic — the X sync and the browser import both end here. Progress lands in the
    job status (`index` → `categorize` steps) that /ui/refresh polls."""
    from . import categorize, storage, usage
    from .ai import BedrockAIClient
    from .search import index_posts

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

    for e in ai.pop_usage():  # meter the embedding + labeling spend
        storage.record_usage(con, e["model"], e["input_tokens"], e["output_tokens"],
                             usage.cost_of(e["model"], e["input_tokens"], e["output_tokens"]))


def _run_auto_answer(cfg: Config, tenant_id: str) -> None:
    """Generate on an isolated tenant connection; failure never changes sync success."""
    from . import autoanswer, storage, usage
    from .deps import make_ai_client
    from .storage import connect

    con = None
    ai = None
    try:
        con = connect(cfg.app_database_url, tenant_id)
        state = autoanswer.load(con)
        if not state or state.get("status") != "pending":
            return
        ai = make_ai_client(cfg)
        autoanswer.generate(con, ai, state["q"])
        logger.info("funnel.auto_answer_ready tenant=%s", tenant_id)
    except Exception as exc:
        logger.warning("funnel.auto_answer_failed tenant=%s exception=%s",
                       tenant_id, type(exc).__name__)
        if con is not None:
            try:
                con.rollback()
                autoanswer.save_failed(con)
            except Exception:
                try:
                    con.rollback()
                except Exception:
                    pass
    finally:
        if ai is not None and con is not None:
            # A DB-originated generation error leaves psycopg's transaction aborted. Recover
            # again after the failed-state write attempt so usage metering remains possible.
            try:
                con.rollback()
            except Exception:
                pass
            for event in ai.pop_usage():
                try:
                    storage.record_usage(
                        con,
                        event["model"],
                        event["input_tokens"],
                        event["output_tokens"],
                        usage.cost_of(event["model"], event["input_tokens"],
                                      event["output_tokens"]),
                    )
                except Exception:
                    logger.warning("funnel.auto_answer_metering_failed tenant=%s", tenant_id)
        if con is not None:
            con.close()


def _maybe_start_auto_answer(cfg: Config, tenant_id: str, con) -> bool:
    """Claim synchronously before done; the DB claim, not process memory, deduplicates work."""
    from . import autoanswer, storage

    if not cfg.auto_answer_enabled_for(tenant_id):
        return False
    claimed = False
    try:
        if storage.get_state(con, autoanswer.STATE_KEY) is not None:
            return False
        eligibility = autoanswer.eligible(con)
        if eligibility.reason:
            logger.info("funnel.auto_answer_skipped tenant=%s reason=%s",
                        tenant_id, eligibility.reason)
            return False
        if not autoanswer.claim(con, eligibility.question or ""):
            return False
        claimed = True
        logger.info("funnel.auto_answer_claimed tenant=%s", tenant_id)
        threading.Thread(target=_run_auto_answer, args=(cfg, tenant_id), daemon=True).start()
        return True
    except Exception as exc:
        logger.warning("funnel.auto_answer_start_failed tenant=%s exception=%s",
                       tenant_id, type(exc).__name__)
        try:
            con.rollback()
            if claimed:
                autoanswer.save_failed(con)
        except Exception:
            try:
                con.rollback()
            except Exception:
                pass
        return False


# One credits-exhausted email per window, not one per failed sync: the 2026-07-14 outage saw
# 35 attempts in a day; the first alert is the actionable one (top up in the X dev console).
_CREDITS_ALERT_WINDOW_S = 6 * 3600
_last_credits_alert = 0.0


def _alert_x_credits_exhausted(cfg: Config) -> None:
    global _last_credits_alert
    with _lock:
        if time.time() - _last_credits_alert < _CREDITS_ALERT_WINDOW_S:
            return
        _last_credits_alert = time.time()
    from . import mail
    mail.send_owner_alert(
        "🚨 X API credits exhausted — bookmark syncs are failing",
        "The X API is returning 402 Payment Required: the developer account is out of API "
        "credits, and every X sync fails until it is topped up. Add credits in the X developer "
        "console; affected users can then just press Sync again. (At most one of these alerts "
        "every 6 hours.)",
        ses_sender=cfg.ses_sender, owner_email=cfg.owner_alert_email, region=cfg.aws_region)


def _run(cfg: Config, tenant_id: str) -> None:
    from . import storage, xapi, xauth
    from .storage import connect

    con = None
    logger.info("sync.start tenant=%s", tenant_id)
    try:
        con = connect(cfg.app_database_url, tenant_id)  # restricted role: RLS-scoped tenant

        _set(tenant_id, step="backfill", detail="fetching new bookmarks from X…")
        # Entitlement cap: free slice + purchased import_limit (None = unlimited/comped).
        cap = storage.effective_import_cap(con, cfg.free_bookmark_limit)
        n_posts = storage.post_count(con, "x")  # entitlement counts X posts only
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

        if storage.post_count(con, "x") == 0:
            # Brand-new X accounts can have ZERO bookmarks — nothing to embed or categorize.
            # A friendly done-state beats a stack trace (two live signups hit this 2026-07-13).
            # (X-scoped: a browser-only library was already processed by its import job.)
            _maybe_start_auto_answer(cfg, tenant_id, con)
            _set(tenant_id, step="done",
                 detail="no bookmarks found on your X account yet — save a few on X, then sync again")
            logger.info("sync.done tenant=%s added=0 empty_library=true", tenant_id)
            return

        _embed_and_label(cfg, con, tenant_id)

        _maybe_start_auto_answer(cfg, tenant_id, con)
        _set(tenant_id, step="done", detail=f"up to date — {added} new bookmark(s) added")
        logger.info("sync.done tenant=%s added=%d", tenant_id, added)
        if storage.is_capped_free(con, cfg.free_bookmark_limit):
            total = storage.post_count(con, "x")
            logger.info("funnel.cap_hit tenant=%s posts=%d", tenant_id, total)
    except xauth.XAuthExpired:  # dead X token → prompt reconnect, not a raw error
        logger.warning("sync.reconnect_needed tenant=%s", tenant_id)
        _set(tenant_id, step="error", error="x_connection_expired")
    except httpx.HTTPStatusError as e:
        # X API failures are OUR platform's problem, never the user's — and the raw httpx
        # message embeds the request URL (their X user id). Sanitized sentinel/status only.
        code = e.response.status_code
        if code == 402:  # developer-account API credits exhausted (2026-07-14: 14 signups lost)
            logger.error("sync.x_credits_exhausted tenant=%s", tenant_id)
            _alert_x_credits_exhausted(cfg)
            _set(tenant_id, step="error", error="x_api_credits")
        else:
            logger.exception("sync.error tenant=%s: %s", tenant_id, e)
            _set(tenant_id, step="error",
                 error=f"X returned an error (HTTP {code}) — please try again in a few minutes.")
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


def _run_source(cfg: Config, tenant_id: str, source: str) -> None:
    """Run a registered source through the shared embed/label pipeline."""
    from . import sources, storage
    from .storage import connect

    con = None
    try:
        con = connect(cfg.app_database_url, tenant_id)
        adapter = sources.get_adapter(source)
        label = sources.source_label(source)
        _set(tenant_id, step="backfill", detail=f"fetching saved items from {label}…")
        cap = storage.effective_import_cap(con, cfg.free_bookmark_limit) if source == "x" else None
        added = adapter.backfill(con, cfg, incremental=True, max_total=cap)
        _set(tenant_id, added=added)
        if storage.post_count(con, source) == 0:
            _maybe_start_auto_answer(cfg, tenant_id, con)
            _set(tenant_id, step="done",
                 detail=f"no saved items found on your {label} account yet")
            return
        _embed_and_label(cfg, con, tenant_id)
        _maybe_start_auto_answer(cfg, tenant_id, con)
        _set(tenant_id, step="done",
             detail=f"{label} is up to date — {added} new saved item(s) added")
    except Exception as exc:
        logger.exception("sync.error tenant=%s source=%s: %s", tenant_id, source, exc)
        _set(tenant_id, step="error", error=f"{type(exc).__name__}: {exc}")
    finally:
        if con is not None:
            con.close()
        _set(tenant_id, running=False, finished_at=time.time())


def start_source(tenant_id: str, source: str) -> bool:
    """Start one configured, connected registry source in the tenant's single sync slot."""
    from . import sources
    from .storage import connect

    cfg = Config.from_env()
    adapter = sources.get_adapter(source)
    with _lock:
        if _jobs.get(tenant_id, {}).get("running"):
            return False
        _jobs[tenant_id] = dict(_IDLE)
        _jobs[tenant_id].update({"running": True, "step": "starting", "detail": source,
                                 "started_at": time.time(), "finished_at": None})
    try:
        adapter = sources.get_configured_adapter(source, cfg)
    except sources.SourceNotConfiguredError as exc:
        _set(tenant_id, running=False, step="error", error=str(exc), finished_at=time.time())
        return False
    con = connect(cfg.app_database_url, tenant_id)
    try:
        connected = adapter.is_connected(con)
    finally:
        con.close()
    if not connected:
        _set(tenant_id, running=False, step="error",
             error=f"Not connected to {sources.source_label(source)} yet — connect it from Sync first.",
             finished_at=time.time())
        return False
    threading.Thread(target=_run_source, args=(cfg, tenant_id, source), daemon=True).start()
    return True


def _run_enrich(cfg: Config, tenant_id: str, added: int) -> None:
    """Embed + label a fresh browser import (the upsert already happened in the request)."""
    from .storage import connect

    con = None
    logger.info("import.enrich.start tenant=%s added=%d", tenant_id, added)
    try:
        con = connect(cfg.app_database_url, tenant_id)
        _embed_and_label(cfg, con, tenant_id)
        _maybe_start_auto_answer(cfg, tenant_id, con)
        _set(tenant_id, added=added, step="done",
             detail=f"{added} browser bookmark(s) imported, embedded & labeled")
        logger.info("import.enrich.done tenant=%s added=%d", tenant_id, added)
    except Exception as e:
        logger.exception("import.enrich.error tenant=%s: %s", tenant_id, e)
        _set(tenant_id, step="error", error=f"{type(e).__name__}: {e}")
    finally:
        if con is not None:
            con.close()
        _set(tenant_id, running=False, finished_at=time.time())


def start_enrich(tenant_id: str, added: int) -> bool:
    """Kick off embed+label for an already-upserted browser import. Same per-tenant job slot
    as the X sync (so /ui/refresh shows its progress and the two can't stomp each other), but
    no X connection required — the data is already in the DB."""
    cfg = Config.from_env()
    with _lock:
        if _jobs.get(tenant_id, {}).get("running"):
            return False
        _jobs[tenant_id] = dict(_IDLE)
        _jobs[tenant_id].update({"running": True, "step": "starting",
                                 "detail": f"processing {added} imported bookmark(s)…",
                                 "started_at": time.time(), "finished_at": None})
    threading.Thread(target=_run_enrich, args=(cfg, tenant_id, added), daemon=True).start()
    return True
