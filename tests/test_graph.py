"""On-demand personal knowledge graph builder tests."""

import numpy as np

from xbb import categorize, graph, storage
from xbb.config import DEFAULT_TENANT_ID
from xbb.search import index_posts


def _seed_graph(seeded_db, fake_ai):
    con = storage.connect(seeded_db)
    index_posts(con, fake_ai)
    categorize.save_taxonomy(con, [
        {"name": "RAG", "definition": "retrieval"},
        {"name": "Agents", "definition": "agents"},
    ])
    # Explicit parent values make both radial communities deterministic.
    con.execute("UPDATE categories SET parent = 'AI & Engineering' WHERE name = 'RAG'")
    con.execute("UPDATE categories SET parent = 'Science & Industry' WHERE name = 'Agents'")
    con.commit()
    categorize.assign_unassigned(con, fake_ai)
    return con


def test_build_graph_has_root_themes_and_all_edge_kinds(seeded_db, fake_ai):
    con = _seed_graph(seeded_db, fake_ai)
    try:
        data = graph.build_graph(con, sim_threshold=0.0)
    finally:
        con.close()
    nodes = {node["id"]: node for node in data["nodes"]}
    edges = data["edges"]
    roots = [node for node in data["nodes"] if node["type"] == "user"]
    assert roots == [{"id": "user:me", "type": "user", "label": "You", "count": 3}]
    assert all(key not in roots[0] for key in ("tenant_id", "email", "name", "photo"))
    themes = [node for node in data["nodes"] if node["type"] == "theme"]
    assert {node["label"] for node in themes} == {"AI & Engineering", "Science & Industry"}
    assert all(node["color"] == graph._parent_color(node["label"]) for node in themes)
    assert all(sum(e["kind"] == "ownership" and e["source"] == "user:me" and
                   e["target"] == node["id"] for e in edges) == 1 for node in themes)
    posts = [node for node in data["nodes"] if node["type"] == "post"]
    assert len(posts) == 3 and all(node["id"].startswith("post:") for node in posts)
    assert all(sum(e["kind"] == "theme" and e["source"] == node["id"]
                   for e in edges) == 1 for node in posts)
    categories = [node for node in data["nodes"] if node["type"] == "category"]
    assert categories and all(node["id"].startswith("cat:") for node in categories)
    memberships = [edge for edge in edges if edge["kind"] == "membership"]
    assert memberships and all(edge["source"] in nodes and edge["target"] in nodes
                               for edge in memberships)
    similarities = [edge for edge in edges if edge["kind"] == "similarity"]
    assert similarities
    assert len({tuple(sorted((e["source"], e["target"]))) for e in similarities}) == len(similarities)
    assert similarities == sorted(similarities, key=lambda e: -e["weight"])


def test_graph_caps_top_ranked_posts_and_similarity_edges(seeded_db, fake_ai):
    con = _seed_graph(seeded_db, fake_ai)
    try:
        expected = {f"post:{row[0]}" for row in con.execute(
            "SELECT id FROM posts ORDER BY bm_rank DESC NULLS LAST, id LIMIT 2").fetchall()}
        data = graph.build_graph(con, node_cap=2, knn_k=1, sim_threshold=0.0, max_edges=1)
    finally:
        con.close()
    assert {n["id"] for n in data["nodes"] if n["type"] == "post"} == expected
    assert data["meta"]["post_nodes"] == 2 and data["meta"]["capped"] is True
    similarities = [edge for edge in data["edges"] if edge["kind"] == "similarity"]
    assert len(similarities) <= 1
    assert all(edge["weight"] >= 0.0 for edge in similarities)


def test_graph_include_flags_remove_associated_nodes_and_edges(seeded_db, fake_ai):
    con = _seed_graph(seeded_db, fake_ai)
    try:
        categories_only = graph.build_graph(con, include_posts=False)
        posts_only = graph.build_graph(con, include_categories=False, sim_threshold=0.0)
    finally:
        con.close()
    assert {n["type"] for n in categories_only["nodes"]} <= {"user", "category"}
    assert not categories_only["edges"]
    assert "category" not in {n["type"] for n in posts_only["nodes"]}
    assert "membership" not in {e["kind"] for e in posts_only["edges"]}
    assert {"user", "theme", "post"} <= {n["type"] for n in posts_only["nodes"]}


def test_empty_graph_keeps_generic_user_anchor(db):
    con = storage.connect(db)
    try:
        data = graph.build_graph(con)
    finally:
        con.close()
    assert data["nodes"] == [{"id": "user:me", "type": "user", "label": "You", "count": 0}]
    assert data["edges"] == []
    assert data["meta"]["capped"] is False


def test_graph_is_tenant_scoped_with_restricted_role(db, app_db):
    tenant_b = "00000000-0000-0000-0000-0000000000b2"
    owner = storage.connect(db, DEFAULT_TENANT_ID)
    try:
        vec = np.asarray([1.0] + [0.0] * 1023, dtype=np.float32)
        owner.execute("INSERT INTO posts (id, text) VALUES ('same', 'tenant A')")
        owner.execute("INSERT INTO embeddings (post_id, vector) VALUES ('same', %s)", (vec,))
        owner.execute("SELECT set_config('app.current_tenant', %s, false)", (tenant_b,))
        owner.execute("INSERT INTO posts (id, text) VALUES ('same', 'tenant B')")
        owner.execute("INSERT INTO embeddings (post_id, vector) VALUES ('same', %s)", (vec,))
        owner.commit()
    finally:
        owner.close()
    con = storage.connect(app_db, DEFAULT_TENANT_ID)
    try:
        data = graph.build_graph(con, include_categories=False)
    finally:
        con.close()
    posts = [node for node in data["nodes"] if node["type"] == "post"]
    assert [node["label"] for node in posts] == ["tenant A"]
