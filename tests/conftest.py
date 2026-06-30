"""Shared test fixtures: an isolated Neon test DB, a fake AI client, and a wired TestClient.

Tests run against a dedicated ``neondb_test`` database (created on the same Neon project) so
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

FIXTURES = Path(__file__).parent / "fixtures"

# tenant-owned tables, child-first, so TRUNCATE ... CASCADE order is safe
_TABLES = ("assignments", "embeddings", "self_thread_posts", "posts",
           "authors", "categories", "sync_state")


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def _test_dsn() -> str:
    """The isolated test database DSN, derived from DATABASE_URL by swapping the db name."""
    return os.environ["DATABASE_URL"].replace("/neondb?", "/neondb_test?")


class FakeAI:
    VOCAB = ["rag", "eval", "agents", "exactly", "quote", "original"]

    def embed(self, texts):
        # bias dim (avoids all-zero vectors) + vocab bag-of-words, padded to the vector(1024) column
        out = []
        for t in texts:
            v = [1.0] + [float(t.lower().count(w)) for w in self.VOCAB]
            out.append(v + [0.0] * (1024 - len(v)))
        return out

    def derive_taxonomy(self, samples):
        return [
            {"name": "RAG", "definition": "retrieval-augmented generation"},
            {"name": "Agents", "definition": "agentic systems"},
        ]

    def assign_categories(self, text, taxonomy):
        tl = text.lower()
        names = [c["name"] for c in taxonomy if c["name"].lower() in tl]
        return names or [taxonomy[0]["name"]]

    def assign_categories_batch(self, posts, taxonomy):
        return [self.assign_categories(p["text"], taxonomy) for p in posts]

    def answer(self, question, retrieved):
        ids = [r["id"] for r in retrieved]
        # Deliberately include a non-retrieved id to prove citation filtering.
        return {"answer": "Synthesized answer.", "citations": ids[:1] + ["999_absent"]}


class FakeClient:
    def __init__(self, pages):
        self._pages = pages

    def iter_bookmark_pages(self):
        yield from self._pages


@pytest.fixture(scope="session", autouse=True)
def _ensure_test_db():
    """Create neondb_test (once) and apply the schema before any test runs."""
    if not _HAVE_DB:
        yield
        return
    real = os.environ["DATABASE_URL"]
    with psycopg.connect(real, autocommit=True) as c:
        if not c.execute("SELECT 1 FROM pg_database WHERE datname='neondb_test'").fetchone():
            c.execute("CREATE DATABASE neondb_test")
    storage.init_db(_test_dsn(), DEFAULT_TENANT_ID)
    yield


@pytest.fixture(autouse=True)
def _point_at_test_db(monkeypatch):
    """Point any Config.from_env() during a test at the isolated test DB (cheap, no network)."""
    if _HAVE_DB:
        monkeypatch.setenv("DATABASE_URL", _test_dsn())
    yield


@pytest.fixture
def db() -> str:
    """A clean test database (truncated). Single-arg connect/init_db default the tenant."""
    if not _HAVE_DB:
        pytest.skip("DATABASE_URL not set — skipping DB-backed test")
    test_dsn = _test_dsn()
    con = storage.connect(test_dsn, DEFAULT_TENANT_ID)
    for t in _TABLES:
        con.execute(f"TRUNCATE {t} CASCADE")
    con.commit()
    con.close()
    return test_dsn


@pytest.fixture
def app_db(db) -> str:
    """Restricted-role DSN for the test DB (RLS enforced). Skips if the app role isn't set up."""
    app = os.environ.get("APP_DATABASE_URL")
    if not app:
        pytest.skip("APP_DATABASE_URL not set — run scripts/setup_app_role.py")
    return app.replace("/neondb?", "/neondb_test?")


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
