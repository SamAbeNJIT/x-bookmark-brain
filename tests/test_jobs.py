"""Per-tenant background jobs: concurrent tenants never block each other; statuses isolated."""

import threading
import time

import pytest

from xbb import jobs
from xbb import sources, storage
from xbb import xapi as xapi_module

A = "00000000-0000-0000-0000-00000000aaaa"
B = "00000000-0000-0000-0000-00000000bbbb"
C = "00000000-0000-0000-0000-00000000cccc"


@pytest.fixture(autouse=True)
def _clean_jobs():
    with jobs._lock:
        jobs._jobs.clear()
    yield
    with jobs._lock:
        jobs._jobs.clear()


@pytest.fixture
def fake_run(monkeypatch):
    """Replace the real sync with a controllable long-running fake (no X/Bedrock/DB work)."""
    release = {A: threading.Event(), B: threading.Event()}
    started = {A: threading.Event(), B: threading.Event()}

    def _fake(cfg, tenant_id):
        started[tenant_id].set()
        release[tenant_id].wait(timeout=10)
        jobs._set(tenant_id, step="done", running=False, finished_at=time.time())

    monkeypatch.setattr(jobs, "_run", _fake)
    monkeypatch.setattr(xapi_module, "is_connected", lambda con: True)
    return started, release


def test_concurrent_tenants_do_not_block_each_other(db, fake_run):
    started, release = fake_run
    assert jobs.start(A) is True
    assert started[A].wait(timeout=5)
    assert jobs.start(A) is False          # same tenant: deduped while running
    assert jobs.start(B) is True           # DIFFERENT tenant: runs concurrently (the fix)
    assert started[B].wait(timeout=5)
    assert jobs.status(A)["running"] and jobs.status(B)["running"]
    release[A].set()
    release[B].set()
    for t in (A, B):
        for _ in range(50):
            if not jobs.status(t)["running"]:
                break
            time.sleep(0.1)
        assert jobs.status(t)["running"] is False
        assert jobs.status(t)["step"] == "done"


def test_restart_allowed_after_finish(db, fake_run):
    started, release = fake_run
    assert jobs.start(A) is True
    started[A].wait(timeout=5)
    release[A].set()
    for _ in range(50):
        if not jobs.status(A)["running"]:
            break
        time.sleep(0.1)
    release[A].clear()
    started[A].clear()
    assert jobs.start(A) is True           # finished job doesn't wedge the tenant


def test_status_isolation(db):
    jobs._set(A, step="backfill", detail="tenant A only")
    assert jobs.status(A)["detail"] == "tenant A only"
    assert jobs.status(B)["step"] == "idle"          # untouched tenant sees idle defaults
    assert jobs.status(B)["detail"] == ""


def test_not_connected_error_is_per_tenant(db, monkeypatch, fake_run):
    started, release = fake_run
    monkeypatch.setattr(xapi_module, "is_connected", lambda con: False)
    assert jobs.start(C) is False
    s = jobs.status(C)
    assert s["running"] is False and "Connect X" in (s["error"] or "")
    assert jobs.status(A)["step"] == "idle"          # other tenants unaffected


def _http_error(code):
    import httpx
    req = httpx.Request("GET", "https://api.twitter.com/2/users/12345/bookmarks?max_results=100")
    return httpx.HTTPStatusError(f"{code}", request=req,
                                 response=httpx.Response(code, request=req))


@pytest.fixture
def sync_that_raises(monkeypatch):
    """Drive the real _run with a backfill that raises; no X/Bedrock/DB work."""
    from xbb import mail

    class Con:
        def close(self):
            pass

    alerts = []
    monkeypatch.setattr(storage, "connect", lambda dsn, tenant: Con())
    monkeypatch.setattr(storage, "effective_import_cap", lambda con, free: 100)
    monkeypatch.setattr(storage, "post_count", lambda con, source: 3)
    monkeypatch.setattr(storage, "import_limit", lambda con: 0)
    monkeypatch.setattr(mail, "send_owner_alert",
                        lambda subject, body, **kw: alerts.append(subject))
    monkeypatch.setattr(jobs, "_last_credits_alert", 0.0)

    def _arm(exc):
        monkeypatch.setattr(xapi_module, "backfill_via_api",
                            lambda *a, **kw: (_ for _ in ()).throw(exc))
    return _arm, alerts


def test_x_402_sets_sentinel_and_alerts_owner_once(sync_that_raises):
    from xbb.config import Config
    arm, alerts = sync_that_raises
    arm(_http_error(402))
    cfg = Config.from_env()
    jobs._run(cfg, A)
    s = jobs.status(A)
    assert s["error"] == "x_api_credits" and s["running"] is False
    assert alerts == ["🚨 X API credits exhausted — bookmark syncs are failing"]
    jobs._run(cfg, B)                                  # second failure inside the window
    assert jobs.status(B)["error"] == "x_api_credits"
    assert len(alerts) == 1                            # deduped: one email per window


def test_other_x_http_errors_hide_the_request_url(sync_that_raises):
    from xbb.config import Config
    arm, alerts = sync_that_raises
    arm(_http_error(503))
    jobs._run(Config.from_env(), A)
    err = jobs.status(A)["error"]
    assert "HTTP 503" in err and "try again" in err
    assert "api.twitter.com" not in err and "12345" not in err  # no URL, no X user id
    assert alerts == []                                # only 402 pages the owner


def test_generic_source_job_passes_no_x_cap_to_non_x(monkeypatch):
    calls = []

    class Adapter:
        def backfill(self, con, cfg, *, incremental, max_total):
            calls.append((incremental, max_total))
            return 2

    class Con:
        def close(self):
            pass

    monkeypatch.setitem(sources.REGISTRY, "reddit-test", Adapter())
    monkeypatch.setattr(storage, "connect", lambda dsn, tenant: Con())
    monkeypatch.setattr(storage, "post_count", lambda con, source: 2)
    monkeypatch.setattr(jobs, "_embed_and_label", lambda cfg, con, tenant: None)
    jobs._run_source(__import__("xbb.config", fromlist=["Config"]).Config.from_env(), A,
                     "reddit-test")
    assert calls == [(True, None)]
    assert jobs.status(A)["step"] == "done"


def test_auto_answer_claim_spawns_once_and_raw_state_skips_eligibility(db, monkeypatch):
    from xbb import autoanswer
    from xbb.config import Config, DEFAULT_TENANT_ID

    con = storage.connect(db)
    for i in range(5):
        con.execute("INSERT INTO posts (id, text) VALUES (%s, %s)", (str(i), f"post {i}"))
    category_id = con.execute(
        "INSERT INTO categories (name, parent) VALUES ('RAG', 'AI') RETURNING id"
    ).fetchone()[0]
    for i in range(3):
        con.execute("INSERT INTO assignments (post_id, category_id) VALUES (%s, %s)",
                    (str(i), category_id))
    con.commit()
    monkeypatch.setenv("AUTO_ANSWER_ENABLED", "true")
    spawned = []

    class ImmediateRecordThread:
        def __init__(self, *, target, args, daemon):
            spawned.append((target, args, daemon))

        def start(self):
            pass

    monkeypatch.setattr(threading, "Thread", ImmediateRecordThread)
    cfg = Config.from_env()
    try:
        assert jobs.status(DEFAULT_TENANT_ID)["step"] == "idle"
        assert jobs._maybe_start_auto_answer(cfg, DEFAULT_TENANT_ID, con) is True
        assert autoanswer.load(con)["status"] == "pending"
        assert len(spawned) == 1 and spawned[0][2] is True
        storage.set_state(con, autoanswer.STATE_KEY, "malformed-but-terminal")
        assert autoanswer.load(con) is None
        monkeypatch.setattr(autoanswer, "eligible", lambda con: (_ for _ in ()).throw(
            AssertionError("raw state must short-circuit eligibility")))
        assert jobs._maybe_start_auto_answer(cfg, DEFAULT_TENANT_ID, con) is False
        assert len(spawned) == 1
    finally:
        con.close()


def test_auto_answer_rollout_modes_are_tenant_enforced(monkeypatch):
    from xbb.config import Config

    monkeypatch.setenv("OWNER_TENANT_ID", A)
    monkeypatch.setenv("AUTO_ANSWER_MODE", "off")
    cfg = Config.from_env()
    assert not cfg.auto_answer_enabled_for(A)
    assert not cfg.auto_answer_enabled_for(B)

    monkeypatch.setenv("AUTO_ANSWER_MODE", "owner")
    cfg = Config.from_env()
    assert cfg.auto_answer_enabled_for(A)
    assert not cfg.auto_answer_enabled_for(B)
    monkeypatch.delenv("OWNER_TENANT_ID")
    assert not Config.from_env().auto_answer_enabled_for(A)
    monkeypatch.setenv("OWNER_TENANT_ID", A)

    monkeypatch.setenv("AUTO_ANSWER_MODE", "all")
    cfg = Config.from_env()
    assert cfg.auto_answer_enabled_for(A)
    assert cfg.auto_answer_enabled_for(B)

    monkeypatch.delenv("AUTO_ANSWER_MODE")
    monkeypatch.setenv("AUTO_ANSWER_ENABLED", "true")
    assert Config.from_env().auto_answer_mode == "all"

    monkeypatch.setenv("AUTO_ANSWER_MODE", "unexpected")
    with pytest.raises(ValueError, match="off, owner, all"):
        Config.from_env()


@pytest.mark.parametrize("failure_point", ["eligibility", "claim", "thread_start"])
def test_auto_answer_start_path_failure_does_not_change_enrichment_success(
        monkeypatch, caplog, failure_point):
    from xbb import autoanswer
    from xbb.config import Config

    rollbacks = []
    failed_states = []

    class Con:
        def rollback(self):
            rollbacks.append(True)

        def close(self):
            pass

    class FailingThread:
        def __init__(self, *, target, args, daemon):
            pass

        def start(self):
            if failure_point == "thread_start":
                raise RuntimeError("sensitive thread detail")

    monkeypatch.setenv("AUTO_ANSWER_MODE", "all")
    monkeypatch.setattr(storage, "connect", lambda dsn, tenant: Con())
    monkeypatch.setattr(storage, "get_state", lambda con, key: None)
    monkeypatch.setattr(jobs, "_embed_and_label", lambda cfg, con, tenant: None)
    monkeypatch.setattr(autoanswer, "eligible", lambda con: (
        (_ for _ in ()).throw(RuntimeError("sensitive eligibility detail"))
        if failure_point == "eligibility"
        else autoanswer.Eligibility("Question?", None)
    ))
    monkeypatch.setattr(autoanswer, "claim", lambda con, question: (
        (_ for _ in ()).throw(RuntimeError("sensitive claim detail"))
        if failure_point == "claim" else True
    ))
    monkeypatch.setattr(autoanswer, "save_failed", lambda con: failed_states.append(True))
    monkeypatch.setattr(threading, "Thread", FailingThread)

    jobs._run_enrich(Config.from_env(), A, 2)

    assert jobs.status(A)["step"] == "done"
    assert rollbacks
    assert bool(failed_states) is (failure_point == "thread_start")
    assert f"tenant={A} exception=RuntimeError" in caplog.text
    assert "sensitive" not in caplog.text


def test_browser_enrichment_claims_before_done(monkeypatch):
    from xbb.config import Config

    order = []

    class Con:
        def close(self):
            pass

    monkeypatch.setattr(storage, "connect", lambda dsn, tenant: Con())
    monkeypatch.setattr(jobs, "_embed_and_label", lambda cfg, con, tenant: order.append("enriched"))

    def maybe(cfg, tenant, con):
        assert jobs.status(tenant)["step"] != "done"
        order.append("claimed")
        return True

    monkeypatch.setattr(jobs, "_maybe_start_auto_answer", maybe)
    jobs._run_enrich(Config.from_env(), A, 2)
    assert order == ["enriched", "claimed"]
    assert jobs.status(A)["step"] == "done"


def test_auto_answer_worker_meters_usage_without_ask_billing(db, monkeypatch):
    from xbb import autoanswer
    from xbb.config import Config, DEFAULT_TENANT_ID
    from xbb import deps

    con = storage.connect(db)
    assert autoanswer.claim(con, "What did I save about RAG?")
    before_credit = storage.credit_balance(con)
    con.close()

    class MeterAI:
        def pop_usage(self):
            return [{"model": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
                     "input_tokens": 100, "output_tokens": 20}]

    monkeypatch.setattr(deps, "make_ai_client", lambda cfg: MeterAI())
    monkeypatch.setattr(autoanswer.ask_module, "ask", lambda con, ai, question, k: {
        "answer": "Stored answer.", "citations": [], "retrieved": [],
    })
    jobs._run_auto_answer(Config.from_env(), DEFAULT_TENANT_ID)
    con = storage.connect(db)
    try:
        assert autoanswer.load(con)["status"] == "ready"
        assert con.execute("SELECT COUNT(*) FROM usage_events").fetchone()[0] == 1
        assert storage.credit_balance(con) == before_credit
        assert storage.free_asks_used_today(con) == 0
        assert storage.get_state(con, "asks_total") is None
    finally:
        con.close()


def test_auto_answer_worker_persists_failed_once(db, monkeypatch):
    from xbb import autoanswer, deps
    from xbb.config import Config, DEFAULT_TENANT_ID

    con = storage.connect(db)
    assert autoanswer.claim(con, "What did I save about RAG?")
    con.close()

    class NoUsageAI:
        def pop_usage(self):
            return []

    monkeypatch.setattr(deps, "make_ai_client", lambda cfg: NoUsageAI())
    monkeypatch.setattr(autoanswer, "generate",
                        lambda *args: (_ for _ in ()).throw(RuntimeError("model failed")))
    jobs._run_auto_answer(Config.from_env(), DEFAULT_TENANT_ID)
    con = storage.connect(db)
    try:
        assert autoanswer.load(con)["status"] == "failed"
        assert not autoanswer.claim(con, "Retry is forbidden")
    finally:
        con.close()


def test_auto_answer_worker_rolls_back_db_failure_then_persists_failed_and_meters(
        db, monkeypatch, caplog):
    from xbb import autoanswer, deps
    from xbb.config import Config, DEFAULT_TENANT_ID

    con = storage.connect(db)
    assert autoanswer.claim(con, "What did I save about RAG?")
    con.close()

    class MeterAI:
        def pop_usage(self):
            return [{"model": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
                     "input_tokens": 100, "output_tokens": 20}]

    def fail_inside_transaction(con, ai, question):
        con.execute("SELECT * FROM auto_answer_missing_relation")

    monkeypatch.setattr(deps, "make_ai_client", lambda cfg: MeterAI())
    monkeypatch.setattr(autoanswer, "generate", fail_inside_transaction)

    jobs._run_auto_answer(Config.from_env(), DEFAULT_TENANT_ID)

    con = storage.connect(db)
    try:
        assert autoanswer.load(con)["status"] == "failed"
        assert con.execute("SELECT COUNT(*) FROM usage_events").fetchone()[0] == 1
        assert (f"funnel.auto_answer_failed tenant={DEFAULT_TENANT_ID} "
                "exception=UndefinedTable") in caplog.text
        assert "auto_answer_missing_relation" not in caplog.text
    finally:
        con.close()
