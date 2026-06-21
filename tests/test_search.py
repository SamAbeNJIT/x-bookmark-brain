"""Semantic search tests (issue #4) with a fake AI client.

The fake embeds text as a bag-of-words vector over a fixed vocabulary, so cosine
similarity is deterministic and a plain-language query retrieves the expected post —
without any live Bedrock call.
"""

import json
from pathlib import Path

from xbb.ingestion import run_backfill
from xbb.search import index_posts, search
from xbb.storage import connect

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


class FakeAI:
    VOCAB = ["rag", "eval", "agents", "exactly", "quote", "original"]

    def embed(self, texts):
        return [[float(t.lower().count(word)) for word in self.VOCAB] for t in texts]


class FakeClient:
    def __init__(self, pages):
        self._pages = pages

    def iter_bookmark_pages(self):
        yield from self._pages


def _seed(tmp_path):
    db = str(tmp_path / "xbb.db")
    page = [load("original.json"), load("reply.json"), load("quote.json")]
    run_backfill(FakeClient([page]), db)
    return db


def test_index_posts_is_incremental(tmp_path):
    db = _seed(tmp_path)
    con = connect(db)
    try:
        ai = FakeAI()
        assert index_posts(con, ai) == 3  # all three embedded
        assert index_posts(con, ai) == 0  # nothing new to embed on a second pass
    finally:
        con.close()


def test_search_finds_the_relevant_post(tmp_path):
    db = _seed(tmp_path)
    con = connect(db)
    try:
        ai = FakeAI()
        index_posts(con, ai)
        results = search(con, ai, "rag evaluation", k=3)
        assert results[0]["id"] == "1001"  # the RAG post ranks first
        assert results[0]["score"] > results[1]["score"]
    finally:
        con.close()
