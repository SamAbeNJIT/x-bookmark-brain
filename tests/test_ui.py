"""HTML screen tests — same fakes/DI as the JSON API tests."""

from xbb.templates import legend, post_card
from xbb.webui import _source_chips


def test_web_card_gets_deterministic_letter_avatar():
    """Author-less web cards render a colored initial in the avatar slot (no third-party
    favicon fetch — that would leak library domains) and a plain-domain byline."""
    p = {"text": "PEP 8", "source": "browser", "url": "https://www.python.org/dev/peps/"}
    card = post_card(p)
    assert 'class="avatar letter"' in card
    assert ">P</div>" in card                        # initial from the domain, www-stripped
    assert 'style="background:#' in card             # palette color inlined
    assert card == post_card(p)                      # deterministic across calls
    assert ">python.org</a>" in card                 # byline is the bare domain
    assert "🌐 python.org" not in card               # old emoji byline retired
    color = card.split('style="background:')[1][:8]
    other = post_card({"text": "t", "source": "browser", "url": "https://news.ycombinator.com/x"})
    assert ">N</div>" in other                       # different domain, its own initial
    assert f"background:{color}" in card             # same seed → same color (stable hash)


def test_handle_card_without_avatar_falls_back_to_letter():
    """Future Reddit/GitHub cards (handle, no avatar_url) seed the initial from the handle;
    X cards keep the plain empty slot they always had."""
    reddit = post_card({"text": "t", "source": "reddit", "handle": "spez"})
    assert 'class="avatar letter"' in reddit and ">S</div>" in reddit
    assert ">@spez</a>" in reddit
    x = post_card({"text": "t", "source": "x", "handle": "alice"})
    assert 'class="avatar"></div>' in x and "letter" not in x
    with_img = post_card({"text": "t", "source": "browser", "url": "https://a.io/",
                          "avatar_url": "https://img.example/pic.jpg"})
    assert '<img class="avatar"' in with_img         # a real image always wins


def test_source_chip_urls_are_percent_encoded():
    class Rows:
        def fetchall(self):
            return [("x", 2), ("weird&source=x", 1)]

    class Connection:
        def execute(self, sql):
            return Rows()

    html, source = _source_chips(
        Connection(), "weird&source=x", {"parent": "AI & Engineering", "view": "list"}
    )
    assert source == "weird&source=x"
    assert "source=weird%26source%3Dx" in html
    assert "parent=AI+%26+Engineering" in html


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
    assert "For X only" in r.text
    assert "Browser, Reddit, and GitHub remain unlimited and free" in r.text
    assert "each new bookmark uses one import" not in r.text


def test_expired_x_token_shows_reconnect(client, seeded_db):
    """A dead refresh token (jobs sets error=x_connection_expired) must render the friendly
    reconnect prompt + button, not a raw stack trace, and no failing Sync button."""
    from xbb import jobs
    from xbb.config import DEFAULT_TENANT_ID
    jobs._set(DEFAULT_TENANT_ID, step="error", error="x_connection_expired", running=False)
    try:
        r = client.get("/ui/refresh")
        assert r.status_code == 200
        assert "connection expired" in r.text and 'href="/oauth/login"' in r.text
        assert "Reconnect X" in r.text
        assert "x_connection_expired" not in r.text          # sentinel never shown raw
        assert 'action="/ui/refresh"' not in r.text          # no Sync button to re-fail
    finally:
        with jobs._lock:
            jobs._jobs.clear()


def test_x_credit_exhaustion_shows_friendly_state(client, seeded_db):
    """The 402 outage sentinel renders reassuring copy — never the sentinel, a raw status
    code, or an API URL (the 2026-07-14 outage showed users httpx errors with their X id)."""
    from xbb import jobs
    from xbb.config import DEFAULT_TENANT_ID
    jobs._set(DEFAULT_TENANT_ID, step="error", error="x_api_credits", running=False)
    try:
        r = client.get("/ui/refresh")
        assert r.status_code == 200
        assert "nothing is wrong with your account" in r.text
        assert "x_api_credits" not in r.text
        assert "402" not in r.text and "api.twitter.com" not in r.text
        assert 'action="/ui/refresh"' in r.text        # Sync button stays: retry can succeed
    finally:
        with jobs._lock:
            jobs._jobs.clear()


def test_refresh_ui_offers_reconnect_for_connected_users(client, seeded_db):
    from xbb import jobs, storage, xapi
    from xbb.config import DEFAULT_TENANT_ID
    with jobs._lock:
        jobs._jobs.clear()
    con = storage.connect(seeded_db, DEFAULT_TENANT_ID)
    xapi.save_tokens(con, {"access_token": "a", "refresh_token": "r", "expires_in": 7200})
    con.close()
    r = client.get("/ui/refresh")  # now connected → quiet reconnect escape hatch shows
    assert "Reconnect your X account" in r.text and 'href="/oauth/login"' in r.text


def test_refresh_token_raises_xauthexpired_on_400(monkeypatch):
    import httpx
    from xbb import xauth

    class _Resp:
        status_code = 400
        def raise_for_status(self): raise AssertionError("should not reach raise_for_status")
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp())
    import pytest
    with pytest.raises(xauth.XAuthExpired):
        xauth.refresh_token("cid", "dead-token")


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


def test_graph_ui_renders_approved_user_centered_visualization(client, seeded_db):
    client.post("/index")
    client.post("/taxonomy", json={"categories": [{"name": "RAG"}, {"name": "Agents"}]})
    client.post("/assign")
    from xbb.storage import connect
    con = connect(seeded_db)
    con.execute("UPDATE categories SET parent = 'Theme <unsafe>' WHERE name = 'RAG'")
    con.commit()
    con.close()
    r = client.get("/ui/graph")
    assert r.status_code == 200
    assert 'id="graph"' in r.text and 'data-src="/ui/graph/data"' in r.text
    assert 'd3@7.9.0/dist/d3.min.js' in r.text
    assert "fetch('/ui/graph/data')" in r.text
    assert all(token in r.text for token in ("Centered", "Free force", "Similarity ≥",
                                              "Edges: on", "Reset", "Center on me"))
    assert 'data-node-types="user theme post"' in r.text
    assert 'data-edge-kinds="ownership theme similarity membership"' in r.text
    assert 'data-layout="user-centered"' in r.text
    assert 'data-selection-path="post theme user"' in r.text
    assert 'class="legend graph-legend" data-mode="graph"' in r.text
    assert 'data-parent="" aria-pressed="true"' in r.text
    assert 'data-parent="Theme &lt;unsafe&gt;" aria-pressed="false"' in r.text
    assert all(contract in r.text for contract in (
        "function focusContext()", "function renderGraphState()", "function updateGraphState()",
        "function themeAvailable(parent)", "function disableUnavailableThemes()",
        "function reconcileSearchFocus()", "themeEdgeForPost", "data-edge-kind",
        "aria-disabled", "unavailable",
    ))
    assert "setThemeFocus(null)" in r.text and "setThemeFocus(d.label)" in r.text
    assert 'id="graph-fallback"' in r.text and "JavaScript is required" in r.text
    assert "3 bookmarks will appear" in r.text
    assert "Theme &lt;unsafe&gt;" in r.text and "Theme <unsafe>" not in r.text
    assert all(color in r.text for color in ("#5b6cf0", "#a45cd6", "#e05569", "#d99a1c",
                                             "#2faa6f", "#2aa7bd", "#9aa0ab"))
    assert 'href="/ui/graph"' in r.text
    assert "tenant_id" not in r.text and "@example.com" not in r.text


def test_feed_legend_remains_navigation(client):
    markup = legend([("AI & Engineering", 2)])
    assert 'class="legend"' in markup and 'data-mode="graph"' not in markup
    assert 'href="/ui/feed"' in markup
    assert 'href="/ui/feed?parent=AI%20%26%20Engineering"' in markup


def test_graph_pageview_is_logged(client):
    import logging
    from xbb.log import logger as xbb_logger
    seen = []
    handler = logging.Handler()
    handler.emit = lambda record: seen.append(record.getMessage())
    xbb_logger.addHandler(handler)
    try:
        client.get("/ui/graph")
        assert any(message.startswith("ui.view page=/ui/graph tenant=") for message in seen)
    finally:
        xbb_logger.removeHandler(handler)


def test_ask_thread_persists_via_localstorage(client):
    """The POST response saves the thread client-side; the GET page carries the restore
    script; the new-conversation link clears the stored thread."""
    client.post("/index")
    r = client.post("/ui/ask", data={"question": "rag evaluation"})
    assert "localStorage.setItem('xbb_thread'" in r.text          # save after each answer
    assert "localStorage.removeItem('xbb_thread')" in r.text      # new-conversation clears
    r = client.get("/ui/ask")
    assert "localStorage.getItem('xbb_thread'" in r.text          # restore on return
    assert "/ui/ask/restore" in r.text                            # via the server-render route
    r = client.get("/ui/ask", params={"question": "prefilled"})
    assert "localStorage.getItem('xbb_thread'" not in r.text      # fresh intent skips restore


def test_ask_restore_renders_thread_and_side_sources(client):
    """The restored view must include the sources pane (side tweets vanished in the first,
    client-only restore — owner bug report)."""
    import json as _json
    client.post("/index")
    hist = [{"role": "user", "content": "rag evaluation"},
            {"role": "assistant", "content": "You saved a post about RAG evaluation."}]
    srcs = [{"q": "rag evaluation", "ids": ["1001"], "cited": ["1001"]}]
    r = client.post("/ui/ask/restore",
                    data={"history": _json.dumps(hist), "sources": _json.dumps(srcs)})
    assert r.status_code == 200
    assert "You saved a post about RAG evaluation." in r.text     # thread restored
    assert "from: “rag evaluation”" in r.text                     # sources pane restored
    assert "ask-right" in r.text and "★ cited" in r.text          # cards, with badges
    # Degenerate stored state doesn't loop: empty history redirects to a restore-skipping URL.
    r = client.post("/ui/ask/restore", data={"history": "[]"}, follow_redirects=False)
    assert r.status_code == 303 and "question=" in r.headers["location"]


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


def _categorized_feed(seeded_db, fake_ai):
    from xbb import categorize
    from xbb.storage import connect
    con = connect(seeded_db)
    categorize.save_taxonomy(con, [{"name": "RAG"}, {"name": "Agents"}])
    categorize.assign_unassigned(con, fake_ai)
    con.close()


def _capture_xbb_log():
    import logging
    from xbb.log import logger as xbb_logger
    seen = []
    h = logging.Handler()
    h.emit = lambda r: seen.append(r.getMessage())
    xbb_logger.addHandler(h)
    return seen, lambda: xbb_logger.removeHandler(h)


def test_feed_ask_composer_renders_and_instruments(client, seeded_db, fake_ai):
    """Inline composer on the feed (26 feed-first vs 8 ask-first after sync, 2026-07-22):
    GET form → /ui/ask, example rotates through the user's own categories, focus beacon
    wired, and rendering logs funnel.feed_composer_viewed."""
    _categorized_feed(seeded_db, fake_ai)
    seen, cleanup = _capture_xbb_log()
    try:
        html = client.get("/ui/feed").text
        assert 'class="feed-ask" method="get" action="/ui/ask"' in html
        assert 'name="src" value="feed"' in html
        assert "Ask your bookmarks anything" in html
        assert "what did I save about RAG?" in html or "what did I save about Agents?" in html
        assert "composer_focused" in html and "sendBeacon" in html
        assert any(m.startswith("funnel.feed_composer_viewed tenant=") for m in seen)
    finally:
        cleanup()


def test_ask_get_with_question_autostarts(client):
    """?question= (the composer handoff) submits #askform on load — after replaceState so
    refresh/back can't re-charge — and carries the allowlisted src attribution."""
    autostart = "history.replaceState({},'','/ui/ask')"        # unique to the auto block
    r = client.get("/ui/ask", params={"question": "rag evaluation", "src": "feed"})
    assert autostart in r.text and "f.requestSubmit()" in r.text
    assert '"feed"' in r.text                                  # src forwarded into the POST
    r = client.get("/ui/ask", params={"question": "x", "src": "evil"})
    assert '"evil"' not in r.text                              # non-allowlisted src dropped
    r = client.get("/ui/ask")
    assert autostart not in r.text                             # plain visits never auto-fire


def test_feed_composer_funnel_events_and_beacon(client, seeded_db, fake_ai):
    _categorized_feed(seeded_db, fake_ai)
    client.post("/index")
    seen, cleanup = _capture_xbb_log()
    try:
        r = client.post("/ui/ask", data={"question": "rag evaluation", "src": "feed"})
        assert "Synthesized answer" in r.text
        assert any(m.startswith("funnel.feed_composer_submitted tenant=") for m in seen)
        assert any(m.startswith("funnel.feed_composer_answered tenant=") for m in seen)
        assert client.post("/ui/t?e=composer_focused").status_code == 204
        assert any(m.startswith("funnel.feed_composer_focused tenant=") for m in seen)
        n = len(seen)
        assert client.post("/ui/t?e=bogus").status_code == 204  # unknown event: no log, no 500
        assert len([m for m in seen[n:] if m.startswith("funnel.")]) == 0
    finally:
        cleanup()


def test_taxonomy_derive_via_ui(client):
    r = client.post("/ui/taxonomy/derive", follow_redirects=True)
    assert r.status_code == 200
    assert "RAG" in r.text and "Agents" in r.text  # FakeAI proposes these
