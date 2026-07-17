"""Browser-bookmark import: upload route, idempotency, unlimited access, and X isolation.

DB-gated like the other integration suites (skips without DATABASE_URL). The enrich job is
monkeypatched out — embedding/labeling has its own tests; here we assert what lands in posts.
"""

from pathlib import Path

import pytest

from xbb import jobs, storage

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def no_enrich(monkeypatch):
    """Record enrich-job starts instead of spawning Bedrock threads."""
    calls = []
    monkeypatch.setattr(jobs, "start_enrich", lambda tenant, added: calls.append(added) or True)
    return calls


def _upload(client, name="chrome_bookmarks.html"):
    content = (FIXTURES / name).read_bytes()
    return client.post("/ui/import", files={"file": (name, content, "text/html")},
                       follow_redirects=False)


def test_import_page_renders(client):
    r = client.get("/ui/import")
    assert r.status_code == 200
    assert "Export bookmarks" in r.text  # per-browser instructions present


def test_upload_stores_browser_posts_and_starts_enrich(client, seeded_db, no_enrich):
    # Give the seeded X posts real ranks first (prod backfill always assigns bm_rank; the
    # JSON fixtures leave it NULL, and NULLs sort FIRST under ORDER BY ... DESC, which would
    # fake out the interleaving assertion below).
    con = storage.connect(seeded_db)
    con.execute("UPDATE posts SET bm_rank = 10 WHERE bm_rank IS NULL")
    con.commit()
    con.close()
    r = _upload(client)
    assert r.status_code == 303 and r.headers["location"] == "/ui/refresh"
    assert no_enrich == [4]  # chrome fixture: 4 importable links

    con = storage.connect(seeded_db)
    try:
        assert storage.post_count(con, "browser") == 4
        row = con.execute(
            "SELECT text, url, author_id, kind FROM posts "
            "WHERE source = 'browser' AND url LIKE '%pep-0008%'").fetchone()
        assert "PEP 8" in row[0] and "Dev/Python" in row[0]  # folder path is labeling signal
        assert row[2] is None and row[3] == "original"
        # browser rows extend the shared bm_rank space above the X posts (feed interleaves)
        top = con.execute("SELECT source FROM posts ORDER BY bm_rank DESC LIMIT 1").fetchone()
        assert top[0] == "browser"
    finally:
        con.close()


def test_reupload_is_idempotent(client, seeded_db, no_enrich):
    _upload(client)
    r = _upload(client)  # same file again → nothing new, no job, friendly notice
    assert r.status_code == 200
    assert "already in your library" in r.text
    assert no_enrich == [4]  # only the first upload started a job

    con = storage.connect(seeded_db)
    try:
        assert storage.post_count(con, "browser") == 4
    finally:
        con.close()


def _make_free(dsn, import_limit=0):
    from xbb.config import DEFAULT_TENANT_ID
    con = storage.connect(dsn)
    con.execute("UPDATE accounts SET ingestion_paid = false, import_limit = %s WHERE id = %s",
                (import_limit, DEFAULT_TENANT_ID))
    con.commit()
    con.close()


def test_browser_import_is_unlimited_for_free_accounts(client, seeded_db, no_enrich):
    _make_free(seeded_db)
    r = _upload(client)
    assert r.status_code == 303
    con = storage.connect(seeded_db)
    try:
        assert storage.post_count(con, "browser") == 4
    finally:
        con.close()


def test_browser_import_never_consumes_x_import_balance(client, seeded_db, no_enrich):
    _make_free(seeded_db, import_limit=1)
    _upload(client)
    con = storage.connect(seeded_db)
    try:
        assert storage.post_count(con, "browser") == 4
        assert storage.imports_available(con, 100) == 1
        assert storage.effective_import_cap(con, 100) == 101
    finally:
        con.close()


def test_browser_upload_continues_with_empty_x_balance(client, seeded_db, no_enrich):
    _make_free(seeded_db)
    _upload(client)
    r = _upload(client, "firefox_bookmarks.html")
    assert r.status_code == 303
    con = storage.connect(seeded_db)
    try:
        assert storage.post_count(con, "browser") > 4
    finally:
        con.close()


def test_feed_source_filter_and_chips(client, seeded_db, no_enrich, fake_ai):
    """?source= filters the feed; the chip row appears once the library is multi-source."""
    from xbb import categorize
    _upload(client)  # adds browser posts alongside the 3 seeded X posts
    con = storage.connect(seeded_db)
    categorize.save_taxonomy(con, [{"name": "RAG"}, {"name": "Agents"}])
    categorize.assign_unassigned(con, fake_ai)  # feed only shows categorized posts
    con.close()
    html = client.get("/ui/feed").text
    assert "🌐 Web" in html and "𝕏 X" in html           # chips render (multi-source library)
    web_only = client.get("/ui/feed?source=browser").text
    assert "postgresql.org" in web_only                 # a browser card is present
    assert "RAG evaluation" not in web_only             # seeded X tweet text filtered out
    x_only = client.get("/ui/feed?source=x").text
    assert "postgresql.org" not in x_only               # browser cards filtered out
    assert "RAG evaluation" in x_only


def test_feed_accepts_data_driven_future_source(client, seeded_db, fake_ai):
    from xbb import categorize
    con = storage.connect(seeded_db)
    con.execute("INSERT INTO posts (id, source, text, bm_rank) VALUES ('future-1', 'future', 'future saved item', 999)")
    categorize.save_taxonomy(con, [{"name": "RAG"}])
    categorize.assign_unassigned(con, fake_ai)
    con.close()
    response = client.get("/ui/feed?source=future")
    assert "Future" in response.text and "future saved item" in response.text
    assert "RAG evaluation" not in response.text


def test_browser_imports_do_not_consume_x_entitlement(client, seeded_db, no_enrich,
                                                      monkeypatch):
    """The regression that matters: free browser rows must not flip the X upsell predicate
    or eat the paid import slice."""
    con = storage.connect(seeded_db)
    try:
        con.execute("UPDATE accounts SET ingestion_paid = false, import_limit = 0")
        con.commit()
        monkeypatch.setenv("FREE_BOOKMARK_LIMIT", "5")  # 3 X posts seeded → under the cap
        _upload(client)                                  # +4 browser rows (total 7 posts)
        assert storage.post_count(con, "x") == 3
        assert storage.is_capped_free(con, 5) is False   # X count 3 < 5: still uncapped
    finally:
        con.close()


def test_rejects_non_bookmark_file(client, no_enrich):
    r = client.post("/ui/import", files={"file": ("x.html", b"<html><body>hi</body></html>",
                                                  "text/html")})
    assert r.status_code == 200
    assert "Export bookmarks" in r.text and no_enrich == []
