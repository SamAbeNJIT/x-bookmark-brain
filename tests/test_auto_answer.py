"""F1 auto first answer: eligibility, persisted state, grounding, and atomic claims."""

import json
import time
from concurrent.futures import ThreadPoolExecutor

from xbb import autoanswer, storage


def _post(con, post_id: str) -> None:
    con.execute("INSERT INTO posts (id, text) VALUES (%s, %s)", (post_id, f"post {post_id}"))


def _category(con, name: str, post_ids: list[str], parent: str = "Theme") -> int:
    category_id = con.execute(
        "INSERT INTO categories (name, parent) VALUES (%s, %s) RETURNING id",
        (name, parent),
    ).fetchone()[0]
    for post_id in post_ids:
        con.execute("INSERT INTO assignments (post_id, category_id) VALUES (%s, %s)",
                    (post_id, category_id))
    return category_id


def test_pick_question_uses_largest_deterministically_and_enforces_threshold():
    assert autoanswer.pick_question([("Agents", 4, 2), ("RAG", 4, 9)]) == (
        "What did I save about Agents?"
    )
    assert autoanswer.pick_question([("Tiny", 2, 1)]) is None
    assert autoanswer.pick_question([]) is None


def test_eligibility_reasons_and_largest_child_category(db):
    con = storage.connect(db)
    try:
        assert autoanswer.eligible(con).reason == "empty_library"
        for i in range(1, 5):
            _post(con, str(i))
        con.commit()
        assert autoanswer.eligible(con).reason == "tiny_library"
        _post(con, "5")
        con.commit()
        assert autoanswer.eligible(con).reason == "no_categories"
        _category(con, "Parent row", ["1", "2", "3", "4", "5"], parent="")
        con.commit()
        assert autoanswer.eligible(con).reason == "no_categories"
        _category(con, "Small child", ["1", "2"])
        con.commit()
        assert autoanswer.eligible(con).reason == "no_dominant_category"
        _category(con, "RAG", ["1", "2", "3"])
        _category(con, "Agents", ["1", "2", "3", "4"])
        con.commit()
        selected = autoanswer.eligible(con)
        assert selected.reason is None
        assert selected.question == "What did I save about Agents?"
    finally:
        con.close()


def test_state_load_tolerates_malformed_and_detects_stale_pending(db):
    con = storage.connect(db)
    try:
        storage.set_state(con, autoanswer.STATE_KEY, "not-json")
        assert autoanswer.load(con) is None
        storage.set_state(con, autoanswer.STATE_KEY, json.dumps({"v": 1, "status": "wat"}))
        assert autoanswer.load(con) is None
        storage.set_state(con, autoanswer.STATE_KEY, json.dumps({
            "v": 1, "status": "pending", "q": "Question?", "created_at": 100.0,
        }))
        state = autoanswer.load(con)
        assert autoanswer.is_pending_fresh(state, now=144.9)
        assert not autoanswer.is_pending_fresh(state, now=145.0)
    finally:
        con.close()


def test_atomic_claim_is_single_shot_across_connections(db):
    def attempt() -> bool:
        con = storage.connect(db)
        try:
            return autoanswer.claim(con, "What did I save about RAG?")
        finally:
            con.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: attempt(), range(2)))
    assert sorted(outcomes) == [False, True]
    con = storage.connect(db)
    try:
        assert autoanswer.load(con)["status"] == "pending"
    finally:
        con.close()


def test_generate_rewrites_ids_clamps_citations_and_does_not_mutate_ask_billing(
        db, monkeypatch):
    con = storage.connect(db)
    try:
        before_credit = storage.credit_balance(con)
        monkeypatch.setattr(autoanswer.ask_module, "ask", lambda con, ai, question, k: {
            "answer": "Read (11), then 12.",
            "citations": ["11", "not-retrieved"],
            "retrieved": [{"id": "11"}, {"id": "12"}],
        })
        state = autoanswer.generate(con, object(), "What did I save about RAG?")
        assert state["answer"] == "Read [1], then [2]."
        assert state["citations"] == ["11"]
        assert state["retrieved_ids"] == ["11", "12"]
        assert storage.credit_balance(con) == before_credit
        assert storage.free_asks_used_today(con) == 0
        assert storage.get_state(con, "asks_total") is None
        assert autoanswer.load(con) == state
    finally:
        con.close()


def test_failed_state_is_terminal_and_well_formed(db):
    con = storage.connect(db)
    try:
        autoanswer.save_failed(con, now=time.time())
        assert autoanswer.load(con)["status"] == "failed"
        assert not autoanswer.claim(con, "Try again?")
    finally:
        con.close()
