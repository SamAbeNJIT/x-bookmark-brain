"""Cap-hit monetization funnel: "complete your library" surfaces appear for capped free
accounts only, the first-answer card exactly once, and every CTA routes through the logged
/ui/complete-library chokepoint. (Seeded corpus = 3 posts; FREE_BOOKMARK_LIMIT=3 makes the
default tenant cap-hit without fixture surgery.)"""

import logging

import pytest

from xbb import jobs, storage
from xbb.config import DEFAULT_TENANT_ID
from xbb.log import logger as xbb_logger

BANNER = "Complete your library to search everything"
CARD = "Complete my library"
POST_SYNC = "You have more bookmarks waiting"


@pytest.fixture
def log_capture():
    seen: list[str] = []
    h = logging.Handler()
    h.emit = lambda r: seen.append(r.getMessage())
    xbb_logger.addHandler(h)
    yield seen
    xbb_logger.removeHandler(h)


def _make_free(dsn, import_limit=0):
    con = storage.connect(dsn)
    con.execute(
        "UPDATE accounts SET ingestion_paid = false, import_limit = %s WHERE id = %s",
        (import_limit, DEFAULT_TENANT_ID))
    con.commit()
    con.close()


@pytest.fixture
def capped(client, seeded_db, monkeypatch):
    """Default tenant becomes a cap-hit free account: 3 posts >= FREE_BOOKMARK_LIMIT=3."""
    monkeypatch.setenv("FREE_BOOKMARK_LIMIT", "3")
    _make_free(seeded_db)
    with jobs._lock:
        jobs._jobs.clear()
    return client


def test_under_limit_user_sees_no_prompts(client, seeded_db):
    # Free account but 3 posts < the default 100 limit -> provably uncapped, zero prompts.
    _make_free(seeded_db)
    client.post("/index")
    assert BANNER not in client.get("/ui/ask").text
    assert BANNER not in client.get("/ui/feed").text
    assert CARD not in client.post("/ui/ask", data={"question": "rag evaluation"}).text


def test_capped_post_sync_message(capped):
    jobs._set(DEFAULT_TENANT_ID, step="done", detail="up to date — 3 new bookmark(s) added")
    html = capped.get("/ui/refresh").text
    assert "Your newest 3 bookmarks" in html and POST_SYNC in html
    # Ask stays the primary CTA; the payment PITCH stays off this screen (value first).
    # (The page's pre-existing footer note mentioning per-bookmark pricing is fine.)
    assert "Ask your first question" in html
    assert CARD not in html and "Complete library →" not in html


def test_first_answer_card_appears_exactly_once(capped):
    capped.post("/index")
    first = capped.post("/ui/ask", data={"question": "rag evaluation"})
    assert CARD in first.text and "searched your newest 3 bookmarks" in first.text
    second = capped.post("/ui/ask", data={"question": "agents"})
    assert CARD not in second.text  # one-time: value was already pitched


def test_capped_banner_on_ask_and_feed(capped):
    capped.post("/index")
    assert BANNER in capped.get("/ui/ask").text
    assert BANNER in capped.get("/ui/feed").text


def test_purchased_user_sees_no_prompts(client, seeded_db, monkeypatch):
    monkeypatch.setenv("FREE_BOOKMARK_LIMIT", "3")
    _make_free(seeded_db, import_limit=500)  # bought an import -> not capped, ever
    with jobs._lock:
        jobs._jobs.clear()
    client.post("/index")
    assert BANNER not in client.get("/ui/ask").text
    assert BANNER not in client.get("/ui/feed").text
    assert CARD not in client.post("/ui/ask", data={"question": "rag evaluation"}).text
    jobs._set(DEFAULT_TENANT_ID, step="done", detail="up to date — 0 new bookmark(s) added")
    assert POST_SYNC not in client.get("/ui/refresh").text


def test_complete_library_route_logs_once_and_redirects(capped, log_capture):
    r = capped.get("/ui/complete-library?src=banner_ask", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/ui/billing?src=banner_ask"
    clicks = [m for m in log_capture if m.startswith("funnel.complete_library_clicked")]
    assert len(clicks) == 1 and "src=banner_ask" in clicks[0]


def test_billing_context_block_shown_when_arriving_from_upsell(capped, monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_dummy")
    html = capped.get("/ui/billing?src=first_answer").text
    assert "Complete your library" in html
    assert "X doesn't reveal your exact total" in html
    assert "unused" in html and "refunds to your card" in html.replace("\n", " ")
    assert "Complete my library" in html          # renamed slider button
    assert "Complete your library" not in capped.get("/ui/billing").text.split("free question")[0]


def test_upsell_viewed_events_carry_surface(capped, log_capture):
    capped.get("/ui/ask")
    capped.get("/ui/feed")
    surfaces = [m for m in log_capture if m.startswith("funnel.upsell_viewed")]
    assert any("surface=banner_ask" in m for m in surfaces)
    assert any("surface=banner_feed" in m for m in surfaces)


def test_is_capped_free_predicate(seeded_db):
    con = storage.connect(seeded_db)
    try:
        con.execute("UPDATE accounts SET ingestion_paid = false, import_limit = 0 "
                    "WHERE id = %s", (DEFAULT_TENANT_ID,))
        con.commit()
        assert storage.is_capped_free(con, 3) is True      # 3 posts >= 3
        assert storage.is_capped_free(con, 100) is False   # 3 posts < 100
        con.execute("UPDATE accounts SET import_limit = 500 WHERE id = %s", (DEFAULT_TENANT_ID,))
        con.commit()
        assert storage.is_capped_free(con, 3) is False     # purchased -> never capped
    finally:
        con.close()


def test_total_asks_counter_increments(db):
    con = storage.connect(db)
    try:
        assert storage.increment_total_asks(con) == 1
        assert storage.increment_total_asks(con) == 2
    finally:
        con.close()
