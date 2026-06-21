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
