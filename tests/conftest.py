"""Shared test fixtures: an isolated Neon test DB, a fake AI client, and a wired TestClient.

Tests run against a dedicated ``xbookmarkbrain_test`` database so
they never touch real data and can truncate freely. The fake AI implements the full
``AIClient`` interface deterministically so logic and endpoints are tested without any live X
or Bedrock call.
"""

import json
import os
from pathlib import Path

import psycopg
import pytest
from dotenv import load_dotenv
from fastapi.testclient import TestClient

from xbb import storage
from xbb.config import DEFAULT_TENANT_ID
from xbb.deps import get_ai, get_db
from xbb.ingestion import run_backfill
from xbb.web import create_app

load_dotenv()  # tests need DATABASE_URL from .env (the CLI/app load it themselves)

# Pure-logic tests (auth, parsing, PKCE) run without a database; DB-backed tests skip cleanly
# when DATABASE_URL is absent (e.g. a GitHub-only checkout with no Neon access).
_HAVE_DB = bool(os.environ.get("DATABASE_URL"))
_DEVELOPMENT_DSN = os.environ.get("DATABASE_URL")
_APP_DEVELOPMENT_DSN = os.environ.get("APP_DATABASE_URL")

FIXTURES = Path(__file__).parent / "fixtures"

# All tenant-owned tables (TRUNCATE ... CASCADE handles FK order). Sourced from storage so it
# can't drift as new tables are added (e.g. usage_events).
_TABLES = storage._TENANT_TABLES


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def _derive_test_dsn(development: str) -> str:
    """Derive and validate the isolated test DSN from any development-role URL."""
    test = storage.replace_database_name(development)
    storage.assert_distinct_database_urls(development, test)
    return test


def _test_dsn() -> str:
    """The isolated owner-role test DSN."""
    return _derive_test_dsn(_DEVELOPMENT_DSN)


class FakeAI:
    VOCAB = ["rag", "eval", "agents", "exactly", "quote", "original"]

    def embed(self, texts):
        # bias dim (avoids all-zero vectors) + vocab bag-of-words, padded to the vector(1024) column
        out = []
        for t in texts:
            v = [1.0] + [float(t.lower().count(w)) for w in self.VOCAB]
            out.append(v + [0.0] * (1024 - len(v)))
        return out

    def group_categories(self, names):
        # Deterministic: everything groups under one fake parent theme.
        return {n: "Test Theme" for n in names}

    def derive_taxonomy(self, samples):
        return [
            {"name": "RAG", "definition": "retrieval-augmented generation"},
            {"name": "Agents", "definition": "agentic systems"},
        ]

    def assign_categories(self, text, taxonomy):
        tl = text.lower()
        names = [c["name"] for c in taxonomy if c["name"].lower() in tl]
        # Vocabulary hits are confident; the fallback first-category guess is not-quite-sure
        # but still above categorize.CONFIDENCE_MIN, so legacy tests keep their assignments.
        if names:
            return [{"name": n, "confidence": 0.9} for n in names]
        return [{"name": taxonomy[0]["name"], "confidence": 0.6}]

    def assign_categories_batch(self, posts, taxonomy):
        return [self.assign_categories(p["text"], taxonomy) for p in posts]

    def rewrite_query(self, question, history):
        # Observable rewrite: fold the last user turn in, so tests can prove the follow-up
        # was contextualized before retrieval.
        prior = [t["content"] for t in history if t["role"] == "user"]
        self.last_rewrite = f"{prior[-1]} {question}" if prior else question
        return self.last_rewrite

    def answer(self, question, retrieved, history=None):
        self.last_history = list(history or [])  # recorded for multi-turn assertions
        ids = [r["id"] for r in retrieved]
        # Leak the cited id into the prose (models do this) to prove the UI's [n] rewrite,
        # and include a non-retrieved id to prove citation filtering. The kept citation is
        # emitted as an INT the way Haiku does — the clamp must coerce, not drop (live bug).
        leak = f" One post ({ids[0]}) covers this." if ids else ""
        kept = [int(ids[0])] if ids and ids[0].isdigit() else ids[:1]
        return {"answer": "Synthesized answer." + leak, "citations": kept + ["999_absent"]}


class FakeClient:
    def __init__(self, pages):
        self._pages = pages

    def iter_bookmark_pages(self, max_results=100):
        yield from self._pages


@pytest.fixture(scope="session", autouse=True)
def _ensure_test_db():
    """Create xbookmarkbrain_test (once) and apply the schema before any test runs."""
    if not _HAVE_DB:
        yield
        return
    real = os.environ["DATABASE_URL"]
    test_dsn = _test_dsn()  # includes the fail-closed identity assertion
    with psycopg.connect(real, autocommit=True) as c:
        if not c.execute("SELECT 1 FROM pg_database WHERE datname=%s",
                         (storage.TEST_DATABASE_NAME,)).fetchone():
            c.execute(f'CREATE DATABASE "{storage.TEST_DATABASE_NAME}"')
    storage.init_db(test_dsn, DEFAULT_TENANT_ID)
    yield


@pytest.fixture(autouse=True)
def _point_at_test_db(monkeypatch):
    """Point Config.from_env() at the test DB and force local fallbacks (no real AWS) in tests."""
    if _HAVE_DB:
        monkeypatch.setenv("DATABASE_URL", _test_dsn())
        # CRITICAL: also repoint the app-role DSN — jobs.start()/deps connect via APP_DATABASE_URL
        # directly, and leaving it on prod let a gate test trigger REAL prod syncs from the suite.
        app = _APP_DEVELOPMENT_DSN
        if app:
            monkeypatch.setenv("APP_DATABASE_URL", _derive_test_dsn(app))
    # Never touch real KMS/SES from the suite — use plaintext tokens + console magic links.
    monkeypatch.delenv("KMS_KEY_ID", raising=False)
    monkeypatch.delenv("SES_SENDER", raising=False)
    monkeypatch.delenv("OWNER_ALERT_EMAIL", raising=False)  # alerts print, never email, in tests
    # Feature flags default off unless the individual test explicitly enables the feature.
    monkeypatch.delenv("AUTO_ANSWER_MODE", raising=False)
    monkeypatch.delenv("AUTO_ANSWER_ENABLED", raising=False)
    # Never fire real X ad-conversion events from the suite: with the live X_ADS_* keys in
    # .env, xconv would otherwise be "configured" and every account-creation test would spawn
    # real API attempts. Tests that need a configured tracker set these explicitly.
    for var in ("X_ADS_PIXEL_ID", "X_ADS_EVENT_ID", "X_ADS_CONSUMER_KEY",
                "X_ADS_CONSUMER_SECRET", "X_ADS_ACCESS_TOKEN", "X_ADS_ACCESS_SECRET"):
        monkeypatch.delenv(var, raising=False)
    yield


@pytest.fixture
def db() -> str:
    """A clean test database (truncated). Single-arg connect/init_db default the tenant."""
    if not _HAVE_DB:
        pytest.skip("DATABASE_URL not set — skipping DB-backed test")
    test_dsn = _test_dsn()
    storage.assert_distinct_database_urls(_DEVELOPMENT_DSN, test_dsn)
    con = storage.connect(test_dsn, DEFAULT_TENANT_ID)
    for t in _TABLES:
        con.execute(f"TRUNCATE {t} CASCADE")
    # Fund the default account so route tests pass the credit gate; credit tests set their own.
    con.execute(
        "UPDATE accounts SET credit_balance_usd = 100, ingestion_paid = true, import_limit = 0 "
        "WHERE id = %s",
        (DEFAULT_TENANT_ID,),
    )
    con.commit()
    con.close()
    return test_dsn


@pytest.fixture
def app_db(db) -> str:
    """Restricted-role DSN for the test DB (RLS enforced). Skips if the app role isn't set up."""
    app = _APP_DEVELOPMENT_DSN
    if not app:
        pytest.skip("APP_DATABASE_URL not set — run scripts/setup_app_role.py")
    return _derive_test_dsn(app)


@pytest.fixture
def fake_ai():
    return FakeAI()


@pytest.fixture
def seeded_db(db) -> str:
    page = [load("original.json"), load("reply.json"), load("quote.json")]
    run_backfill(FakeClient([page]), db)
    return db


@pytest.fixture
def client(seeded_db, fake_ai):
    app = create_app()

    def _db():
        con = storage.connect(seeded_db)
        try:
            yield con
        finally:
            con.close()

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_ai] = lambda: fake_ai
    return TestClient(app)
