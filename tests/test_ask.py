"""Ask mode / RAG (#7): answers cite only retrieved posts."""

from xbb.ask import ask
from xbb.search import index_posts
from xbb.storage import connect


def test_ask_cites_only_retrieved_posts(seeded_db, fake_ai):
    con = connect(seeded_db)
    try:
        index_posts(con, fake_ai)
        result = ask(con, fake_ai, "rag evaluation", k=3)
        assert result["answer"]
        retrieved_ids = {r["id"] for r in result["retrieved"]}
        assert "1001" in retrieved_ids
        assert "999_absent" not in result["citations"]  # fabricated citation filtered out
        assert set(result["citations"]) <= retrieved_ids
    finally:
        con.close()
