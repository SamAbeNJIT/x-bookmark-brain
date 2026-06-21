"""Shared test fixtures: a seeded database, a fake AI client, and a wired TestClient.

The fake AI implements the full `AIClient` interface deterministically so logic and
endpoints can be tested without any live X or Bedrock call.
"""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from xbb.deps import get_ai, get_db
from xbb.ingestion import run_backfill
from xbb.storage import connect
from xbb.web import create_app

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


class FakeAI:
    VOCAB = ["rag", "eval", "agents", "exactly", "quote", "original"]

    def embed(self, texts):
        return [[float(t.lower().count(w)) for w in self.VOCAB] for t in texts]

    def derive_taxonomy(self, samples):
        return [
            {"name": "RAG", "definition": "retrieval-augmented generation"},
            {"name": "Agents", "definition": "agentic systems"},
        ]

    def assign_categories(self, text, taxonomy):
        tl = text.lower()
        names = [c["name"] for c in taxonomy if c["name"].lower() in tl]
        return names or [taxonomy[0]["name"]]

    def answer(self, question, retrieved):
        ids = [r["id"] for r in retrieved]
        # Deliberately include a non-retrieved id to prove citation filtering.
        return {"answer": "Synthesized answer.", "citations": ids[:1] + ["999_absent"]}


class FakeClient:
    def __init__(self, pages):
        self._pages = pages

    def iter_bookmark_pages(self):
        yield from self._pages


@pytest.fixture
def fake_ai():
    return FakeAI()


@pytest.fixture
def seeded_db(tmp_path):
    db = str(tmp_path / "xbb.db")
    page = [load("original.json"), load("reply.json"), load("quote.json")]
    run_backfill(FakeClient([page]), db)
    return db


@pytest.fixture
def client(seeded_db, fake_ai):
    app = create_app()

    def _db():
        con = connect(seeded_db)
        try:
            yield con
        finally:
            con.close()

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_ai] = lambda: fake_ai
    return TestClient(app)
