"""Semantic search tests (issue #4) with a fake AI client.

The fake embeds text as a bag-of-words vector over a fixed vocabulary, so cosine similarity
is deterministic and a plain-language query retrieves the expected post — without any live
Bedrock call. Runs against the isolated Neon test DB + pgvector (see conftest).
"""

from xbb.search import index_posts, search
from xbb.storage import connect


def test_index_posts_is_incremental(seeded_db, fake_ai):
    con = connect(seeded_db)
    try:
        assert index_posts(con, fake_ai) == 3  # all three embedded
        assert index_posts(con, fake_ai) == 0  # nothing new to embed on a second pass
    finally:
        con.close()


def test_search_finds_the_relevant_post(seeded_db, fake_ai):
    con = connect(seeded_db)
    try:
        index_posts(con, fake_ai)
        results = search(con, fake_ai, "rag evaluation", k=3)
        assert results[0]["id"] == "1001"  # the RAG post ranks first
        assert results[0]["score"] > results[1]["score"]
    finally:
        con.close()
