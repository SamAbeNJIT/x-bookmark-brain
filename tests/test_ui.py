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


def test_pageviews_are_logged_server_side(client):
    """GET /ui/* emits a ui.view event (path + tenant only — never content)."""
    import logging
    from xbb.log import logger as xbb_logger
    seen = []
    h = logging.Handler()
    h.emit = lambda r: seen.append(r.getMessage())
    xbb_logger.addHandler(h)
    try:
        client.get("/ui/refresh")
        assert any(m.startswith("ui.view page=/ui/refresh tenant=") for m in seen)
    finally:
        xbb_logger.removeHandler(h)


def test_feed_view_toggle_grid_list_and_cookie(client, seeded_db, fake_ai):
    from xbb import categorize
    from xbb.storage import connect
    con = connect(seeded_db)
    categorize.save_taxonomy(con, [{"name": "RAG"}, {"name": "Agents"}])
    categorize.assign_unassigned(con, fake_ai)       # feed only shows categorized posts
    con.close()
    r = client.get("/ui/feed")                       # default: masonry grid
    assert 'class="cards"' in r.text and "▦ Grid" in r.text and "☰ List" in r.text
    r = client.get("/ui/feed?view=list")             # explicit list view
    assert 'class="cards list"' in r.text
    assert "xbb_feed_view=list" in r.headers.get("set-cookie", "")
    r = client.get("/ui/feed")                       # cookie remembered
    assert 'class="cards list"' in r.text
    r = client.get("/ui/feed?view=grid")             # explicit switch back wins
    assert 'class="cards list"' not in r.text and 'class="cards"' in r.text
    # Category detail pages share the same toggle AND the same cookie preference.
    cid = None
    for line in client.get("/ui/categories").text.split('href="/ui/categories/'):
        if line[:1].isdigit():
            cid = line.split('"')[0]
            break
    assert cid is not None
    r = client.get(f"/ui/categories/{cid}?view=list")
    assert 'class="cards list"' in r.text and "☰ List" in r.text
    r = client.get("/ui/feed")                       # cookie set on category page carries over
    assert 'class="cards list"' in r.text


def test_taxonomy_derive_via_ui(client):
    r = client.post("/ui/taxonomy/derive", follow_redirects=True)
    assert r.status_code == 200
    assert "RAG" in r.text and "Agents" in r.text  # FakeAI proposes these
