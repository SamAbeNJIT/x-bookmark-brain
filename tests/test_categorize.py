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


class _ScriptedAI:
    """Labeler fake with scripted per-call confidence — batch and single-post retry separately."""

    def __init__(self, batch_labels, retry_labels=None):
        self.batch_labels = batch_labels      # name → labels for the batch pass
        self.retry_labels = retry_labels or {}  # text-keyword → labels for the retry pass
        self.retried = []

    def assign_categories_batch(self, posts, taxonomy):
        return [list(self.batch_labels) for _ in posts]

    def assign_categories(self, text, taxonomy):
        self.retried.append(text)
        for key, labels in self.retry_labels.items():
            if key in text.lower():
                return list(labels)
        return []


def test_low_confidence_labels_dropped_to_unsorted(seeded_db):
    """Labels under CONFIDENCE_MIN are not written: the post stays unassigned (the Unsorted
    bucket), is retried once, and is not re-attempted on the next sync."""
    con = connect(seeded_db)
    try:
        categorize.save_taxonomy(con, [{"name": "RAG"}, {"name": "Agents"}])
        ai = _ScriptedAI([{"name": "RAG", "confidence": 0.2}])
        assert categorize.assign_unassigned(con, ai) == 3
        assert len(ai.retried) > 0  # all-weak batch labels triggered the single-post retry
        counts = {c["name"]: c["count"] for c in categorize.categories_with_counts(con)}
        assert counts == {"RAG": 0, "Agents": 0}
        assert categorize.unlabeled_count(con) == 3
        assert categorize.assign_unassigned(con, ai) == 0  # attempted once, never re-sent
    finally:
        con.close()


def test_mixed_confidence_keeps_only_confident_and_stores_score(seeded_db):
    con = connect(seeded_db)
    try:
        categorize.save_taxonomy(con, [{"name": "RAG"}, {"name": "Agents"}])
        ai = _ScriptedAI([{"name": "RAG", "confidence": 0.9},
                          {"name": "Agents", "confidence": 0.3}])
        categorize.assign_unassigned(con, ai)
        counts = {c["name"]: c["count"] for c in categorize.categories_with_counts(con)}
        assert counts == {"RAG": 3, "Agents": 0}
        stored = [r[0] for r in con.execute("SELECT confidence FROM assignments")]
        assert stored == [0.9, 0.9, 0.9]
    finally:
        con.close()


def test_confidence_exactly_at_cutoff_is_kept(seeded_db):
    con = connect(seeded_db)
    try:
        categorize.save_taxonomy(con, [{"name": "RAG"}])
        ai = _ScriptedAI([{"name": "RAG", "confidence": categorize.CONFIDENCE_MIN}])
        categorize.assign_unassigned(con, ai)
        counts = {c["name"]: c["count"] for c in categorize.categories_with_counts(con)}
        assert counts == {"RAG": 3}
    finally:
        con.close()


def test_confident_retry_rescues_weak_batch_label(seeded_db):
    """A labelable post whose batch labels were all weak gets the single-post retry, and a
    confident retry answer IS written."""
    con = connect(seeded_db)
    try:
        categorize.save_taxonomy(con, [{"name": "RAG"}, {"name": "Agents"}])
        ai = _ScriptedAI([{"name": "Agents", "confidence": 0.1}],
                         retry_labels={"rag": [{"name": "RAG", "confidence": 0.8}]})
        categorize.assign_unassigned(con, ai)
        counts = {c["name"]: c["count"] for c in categorize.categories_with_counts(con)}
        assert counts["RAG"] >= 1 and counts["Agents"] == 0
    finally:
        con.close()


def test_derive_taxonomy_empty_corpus_returns_empty_without_ai_call(db):
    """Zero-bookmark libraries must not reach the model (live crash, 2026-07-13: the model
    replies in prose to an empty sample and the JSON parse kills the sync)."""
    class _NeverCalled:
        def derive_taxonomy(self, samples):
            raise AssertionError("model must not be called with an empty sample")

    con = connect(db)
    try:
        assert categorize.derive_taxonomy(con, _NeverCalled()) == []
        categorize.save_taxonomy(con, [])  # no-op, no crash
        assert categorize.get_taxonomy(con) == []
    finally:
        con.close()


def test_derive_parents_groups_unparented_categories(db, fake_ai):
    """New tenants' AI-derived category names never match DEFAULT_PARENTS — derive_parents
    must group them so the tree doesn't collapse into one giant 'Other'."""
    con = connect(db)
    try:
        categorize.save_taxonomy(con, [{"name": "Quantum Basket Weaving", "definition": "x"},
                                       {"name": "Competitive Napping", "definition": "y"}])
        assert categorize.derive_parents(con, fake_ai) == 2
        parents = {r[0]: r[1] for r in con.execute(
            "SELECT name, parent FROM categories WHERE name IN "
            "('Quantum Basket Weaving','Competitive Napping')")}
        assert set(parents.values()) == {"Test Theme"}
        assert categorize.derive_parents(con, fake_ai) == 0  # idempotent no-op once parented
    finally:
        con.close()
