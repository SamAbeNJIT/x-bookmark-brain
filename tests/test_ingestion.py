"""Tests for the ingestion seam (issue #2): parsing recorded payloads + backfill.

These assert external behavior — the parsed record shape and what ends up persisted — not
implementation details. The live GraphQLXClient is not exercised here (it needs real
credentials); a fake client feeds recorded payloads through the same `run_backfill` path.
"""

import json
from pathlib import Path

from xbb.ingestion import parse_bookmark, run_backfill
from xbb.storage import connect

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


class FakeXClient:
    """An XClient that yields pre-recorded pages instead of calling X."""

    def __init__(self, pages: list[list[dict]]) -> None:
        self._pages = pages

    def iter_bookmark_pages(self):
        yield from self._pages


def test_parse_original_post():
    rec = parse_bookmark(load("original.json"))
    assert rec["id"] == "1001"
    assert rec["kind"] == "original"
    assert rec["parent_post_id"] is None
    assert rec["author"] == {"id": "u_alice", "handle": "alice", "display_name": "Alice Researcher"}
    assert rec["url"] == "https://x.com/alice/status/1001"
    assert rec["text"].startswith("Thoughts on RAG")
    assert rec["lang"] == "en"
    assert "RAG" in rec["hashtags"]
    assert rec["links"] == ["https://example.com/rag-eval"]
    assert rec["media"][0]["alt_text"] == "a precision-recall chart"
    assert rec["like_count"] == 42
    assert rec["raw"]  # raw payload retained verbatim


def test_parse_reply_captures_immediate_parent():
    rec = parse_bookmark(load("reply.json"))
    assert rec["kind"] == "reply"
    assert rec["parent_post_id"] == "900"


def test_parse_quote_captures_quoted_id():
    rec = parse_bookmark(load("quote.json"))
    assert rec["kind"] == "quote"
    assert rec["parent_post_id"] == "800"


def test_run_backfill_persists_all_bookmarks(tmp_path):
    db = str(tmp_path / "xbb.db")
    page = [load("original.json"), load("reply.json"), load("quote.json")]
    count = run_backfill(FakeXClient([page]), db)
    assert count == 3
    con = connect(db)
    try:
        posts = con.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
        authors = con.execute("SELECT COUNT(*) FROM authors").fetchone()[0]
        raw = con.execute("SELECT raw_json FROM posts WHERE id='1001'").fetchone()[0]
    finally:
        con.close()
    assert posts == 3
    assert authors == 3
    assert json.loads(raw)["rest_id"] == "1001"  # raw retained and round-trips


def test_run_backfill_is_idempotent(tmp_path):
    db = str(tmp_path / "xbb.db")
    page = [load("original.json"), load("reply.json"), load("quote.json")]
    run_backfill(FakeXClient([page]), db)
    run_backfill(FakeXClient([page]), db)  # second run must not duplicate
    con = connect(db)
    try:
        assert con.execute("SELECT COUNT(*) FROM posts").fetchone()[0] == 3
    finally:
        con.close()
