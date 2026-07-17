"""Endpoint tests for the JSON API, with db + AI seams overridden by fakes."""


def test_search_endpoint_ranks_relevant_post_first(client):
    assert client.post("/index").json()["indexed"] == 3
    resp = client.get("/search", params={"q": "rag evaluation", "k": 3})
    assert resp.status_code == 200
    assert resp.json()["results"][0]["id"] == "1001"


def test_taxonomy_save_assign_browse_endpoints(client):
    resp = client.post("/taxonomy", json={"categories": [{"name": "RAG"}, {"name": "Agents"}]})
    assert resp.status_code == 200
    assert client.post("/assign").json()["processed"] == 3
    counts = {c["name"]: c["count"] for c in client.get("/categories").json()["categories"]}
    assert counts == {"RAG": 1, "Agents": 2}


def test_ask_endpoint_filters_citations(client):
    client.post("/index")
    body = client.post("/ask", json={"question": "rag evaluation", "k": 3}).json()
    assert body["answer"]
    assert "999_absent" not in body["citations"]


def _seed_graph_api(client):
    assert client.post("/index").json()["indexed"] in (0, 3)
    client.post("/taxonomy", json={"categories": [{"name": "RAG"}, {"name": "Agents"}]})
    client.post("/assign")


def test_graph_data_endpoint_contract_caps_and_flags(client):
    _seed_graph_api(client)
    response = client.get("/ui/graph/data", params={"sim_threshold": 0})
    assert response.status_code == 200
    data = response.json()
    assert set(data) == {"nodes", "edges", "meta"}
    assert [n["id"] for n in data["nodes"] if n["type"] == "user"] == ["user:me"]
    themes = [n for n in data["nodes"] if n["type"] == "theme"]
    assert 1 <= len(themes) <= 7
    assert any(n["id"].startswith("post:") for n in data["nodes"])
    assert any(n["id"].startswith("cat:") for n in data["nodes"])
    assert all(any(e["kind"] == "ownership" and e["source"] == "user:me" and
                   e["target"] == theme["id"] for e in data["edges"]) for theme in themes)
    capped = client.get("/ui/graph/data", params={"node_cap": 1}).json()
    assert capped["meta"]["post_nodes"] == 1 and capped["meta"]["capped"] is True
    no_posts = client.get("/ui/graph/data", params={"posts": "false"}).json()
    assert not any(n["type"] == "post" for n in no_posts["nodes"])


def test_graph_data_endpoint_clamps_expensive_inputs(client):
    data = client.get("/ui/graph/data", params={"node_cap": 999999, "knn_k": 999}).json()
    assert data["meta"]["node_cap"] == 1500
    assert data["meta"]["knn_k"] == 20
