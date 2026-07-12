"""HTML screen tests — same fakes/DI as the JSON API tests."""


def test_home_renders(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "bookmarks" in r.text.lower()


def test_search_ui_shows_relevant_post(client):
    client.post("/index")
    r = client.get("/ui/search", params={"q": "rag evaluation"})
    assert r.status_code == 200
    assert "RAG evaluation" in r.text  # text of the top-ranked bookmark


def test_categories_ui_lists_saved_taxonomy(client):
    client.post("/taxonomy", json={"categories": [{"name": "RAG"}, {"name": "Agents"}]})
    r = client.get("/ui/categories")
    assert r.status_code == 200
    assert "RAG" in r.text and "Agents" in r.text


def test_ask_ui_returns_answer(client):
    client.post("/index")
    r = client.post("/ui/ask", data={"question": "rag evaluation"})
    assert r.status_code == 200
    assert "Synthesized answer" in r.text


def test_ask_ui_rewrites_raw_post_ids_to_numbered_refs(client):
    """FakeAI leaks the cited post id into the prose (as real models do); the UI must render
    a [1] marker instead of the raw id, and number the cited card's badge to match."""
    client.post("/index")
    r = client.post("/ui/ask", data={"question": "rag evaluation"})
    assert r.status_code == 200
    assert "(1001)" not in r.text.replace("[1]", "")  # raw id gone from the prose
    assert "[1]" in r.text                            # numbered marker in its place
    assert "★ cited [1]" in r.text                    # matching card badge


def test_refresh_ui_renders(client):
    r = client.get("/ui/refresh")  # GET is side-effect free (POST would trigger a sync)
    assert r.status_code == 200
    assert "Sync" in r.text


def test_taxonomy_derive_via_ui(client):
    r = client.post("/ui/taxonomy/derive", follow_redirects=True)
    assert r.status_code == 200
    assert "RAG" in r.text and "Agents" in r.text  # FakeAI proposes these
