"""Ask mode / RAG (#7): answers cite only retrieved posts; multi-turn via client-held history."""

from xbb.ask import HISTORY_MAX_CHARS, HISTORY_MAX_TURNS, ask, trim_history
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


def test_followup_is_rewritten_and_history_reaches_the_model(seeded_db, fake_ai):
    """A follow-up retrieves on the REWRITTEN query (conversation folded in) and the model
    sees the prior turns."""
    con = connect(seeded_db)
    try:
        index_posts(con, fake_ai)
        history = [{"role": "user", "content": "rag evaluation"},
                   {"role": "assistant", "content": "Here are your RAG bookmarks."}]
        result = ask(con, fake_ai, "which mention evals?", k=3, history=history)
        assert fake_ai.last_rewrite == "rag evaluation which mention evals?"
        assert fake_ai.last_history == history  # prior turns passed to answer()
        assert result["answer"]
        # The rewrite made the vague follow-up retrieve the rag post.
        assert "1001" in {r["id"] for r in result["retrieved"]}
    finally:
        con.close()


def test_first_turn_skips_the_rewrite(seeded_db, fake_ai):
    con = connect(seeded_db)
    try:
        index_posts(con, fake_ai)
        fake_ai.last_rewrite = None
        ask(con, fake_ai, "rag evaluation", k=3)  # no history → no rewrite call
        assert fake_ai.last_rewrite is None
        assert fake_ai.last_history == []
    finally:
        con.close()


def test_rerank_reorders_and_degrades_gracefully():
    from xbb.rerank import rerank

    class _RerankAI:
        def _invoke(self, model, body):
            assert model.startswith("cohere.rerank")
            # reverse the candidate order to prove reordering is applied
            return {"results": [{"index": i} for i in reversed(range(body["top_n"]))]}

    cands = [{"id": str(i), "text": f"post {i}"} for i in range(5)]
    out = rerank(_RerankAI(), "q", cands, 3)
    assert [p["id"] for p in out] == ["2", "1", "0"]

    class _BrokenAI:
        def _invoke(self, model, body):
            raise RuntimeError("rerank down")

    out = rerank(_BrokenAI(), "q", cands, 3)
    assert [p["id"] for p in out] == ["0", "1", "2"]  # falls back to hybrid order


def test_ask_survives_ai_without_rerank_support(seeded_db, fake_ai):
    """FakeAI has no _invoke — rerank must degrade to plain hybrid order, not crash."""
    con = connect(seeded_db)
    try:
        index_posts(con, fake_ai)
        result = ask(con, fake_ai, "rag evaluation", k=3)
        assert result["answer"] and result["retrieved"]
    finally:
        con.close()


def test_trim_history_bounds_and_validates():
    # Malformed / hostile input → dropped; well-formed turns bounded in count and length.
    assert trim_history("nonsense") == []
    assert trim_history([{"role": "system", "content": "evil"}, {"role": "user"}, 42]) == []
    long = [{"role": "user", "content": f"q{i}" * 1} for i in range(20)]
    assert len(trim_history(long)) == HISTORY_MAX_TURNS
    big = trim_history([{"role": "user", "content": "x" * 99999}])
    assert len(big[0]["content"]) == HISTORY_MAX_CHARS
