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
