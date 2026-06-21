"""Taxonomy review (#5) and multi-label assignment + browse (#6) logic tests."""

from xbb import categorize
from xbb.storage import connect


def _ids(con):
    return {c["name"]: c["id"] for c in categorize.get_taxonomy(con)}


def test_save_and_get_taxonomy(seeded_db):
    con = connect(seeded_db)
    try:
        categorize.save_taxonomy(con, [{"name": "RAG", "definition": "x"}, {"name": "Agents"}])
        assert [c["name"] for c in categorize.get_taxonomy(con)] == ["Agents", "RAG"]
    finally:
        con.close()


def test_rename_and_delete(seeded_db):
    con = connect(seeded_db)
    try:
        categorize.save_taxonomy(con, [{"name": "RAG"}])
        categorize.rename_category(con, _ids(con)["RAG"], "Retrieval")
        assert [c["name"] for c in categorize.get_taxonomy(con)] == ["Retrieval"]
        categorize.delete_category(con, _ids(con)["Retrieval"])
        assert categorize.get_taxonomy(con) == []
    finally:
        con.close()


def test_assign_unassigned_and_browse(seeded_db, fake_ai):
    con = connect(seeded_db)
    try:
        categorize.save_taxonomy(con, [{"name": "RAG"}, {"name": "Agents"}])
        assert categorize.assign_unassigned(con, fake_ai) == 3
        counts = {c["name"]: c["count"] for c in categorize.categories_with_counts(con)}
        # 1001 → RAG; 1002 (no keyword → fallback to first category) + 1003 → Agents
        assert counts == {"RAG": 1, "Agents": 2}
        rag_posts = categorize.posts_in_category(con, _ids(con)["RAG"])
        assert {p["id"] for p in rag_posts} == {"1001"}
        assert categorize.assign_unassigned(con, fake_ai) == 0  # nothing left to assign
    finally:
        con.close()


def test_merge_moves_assignments(seeded_db, fake_ai):
    con = connect(seeded_db)
    try:
        categorize.save_taxonomy(con, [{"name": "RAG"}, {"name": "Agents"}])
        categorize.assign_unassigned(con, fake_ai)
        ids = _ids(con)
        categorize.merge_categories(con, ids["Agents"], ids["RAG"])
        assert [c["name"] for c in categorize.get_taxonomy(con)] == ["RAG"]
        counts = {c["name"]: c["count"] for c in categorize.categories_with_counts(con)}
        assert counts == {"RAG": 3}
    finally:
        con.close()
