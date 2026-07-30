"""Real-pagination behavior of XApiClient.iter_bookmark_pages (the _get HTTP seam is faked,
everything above it is real). X omits posts whose author is deleted, suspended, or protected,
so a deep page can come back with EMPTY data yet still carry a next_token — stopping there
silently truncates old libraries (backlog item 22: two paying customers hit the resulting
"where are my bookmarks" confusion on the same day)."""

from xbb import storage, xapi


def _page(ids, token=None):
    page = {"data": [{"id": str(i), "text": f"post {i}"} for i in ids], "includes": {}}
    if token:
        page["meta"] = {"next_token": token}
    return page


def _wire_real_client(monkeypatch, pages):
    """Script the HTTP responses under the REAL client; returns the list of served requests.
    Requesting more pages than scripted raises IndexError — a loud runaway-pagination signal."""
    served = []

    def fake_get(self, path, params=None):
        if path == "/users/me":
            return {"data": {"id": "u1"}}
        served.append(dict(params or {}))
        return pages[len(served) - 1]

    monkeypatch.setattr(xapi.XApiClient, "_get", fake_get)
    monkeypatch.setattr(xapi, "load_tokens", lambda con: {"access_token": "t", "expires_at": 9e12})
    monkeypatch.setattr(xapi.time, "sleep", lambda s: None)
    return served


def test_empty_page_with_next_token_continues(db, monkeypatch, caplog):
    # Page 2 is fully filtered (every post's author gone) but the timeline continues: the
    # posts beyond it must still be reached, and the end must be instrumented.
    served = _wire_real_client(monkeypatch, [
        _page([1, 2], token="t2"),
        _page([], token="t3"),
        _page([3, 4]),  # no next_token — the genuine end of the list
    ])
    con = storage.connect(db)
    try:
        caplog.set_level("INFO", logger="xbb")
        added = xapi.backfill_via_api(con, "cid", incremental=False)
        assert added == 4
        assert len(served) == 3
        assert served[2].get("pagination_token") == "t3"  # the empty page's token was used
        assert "sync.backfill_pages" in caplog.text
        assert "pages=3" in caplog.text
        assert "end_reason=no_token" in caplog.text
    finally:
        con.close()


def test_empty_page_run_is_bounded(db, monkeypatch, caplog):
    # Every request is billed: an API that hands back endless empty-but-tokened pages must be
    # abandoned after _MAX_EMPTY_PAGES consecutive empties, never paged forever.
    pages = [_page([1], token="t1")] + [_page([], token=f"t{i}") for i in range(2, 12)]
    served = _wire_real_client(monkeypatch, pages)
    con = storage.connect(db)
    try:
        caplog.set_level("INFO", logger="xbb")
        added = xapi.backfill_via_api(con, "cid", incremental=False)
        assert added == 1
        assert len(served) == 1 + xapi._MAX_EMPTY_PAGES
        assert "end_reason=empty_pages" in caplog.text
    finally:
        con.close()


def test_single_page_end_unchanged(db, monkeypatch, caplog):
    # The common case — one page, no token — must not gain extra requests.
    served = _wire_real_client(monkeypatch, [_page([1, 2])])
    con = storage.connect(db)
    try:
        caplog.set_level("INFO", logger="xbb")
        assert xapi.backfill_via_api(con, "cid", incremental=False) == 2
        assert len(served) == 1
        assert "end_reason=no_token" in caplog.text
    finally:
        con.close()


def test_capped_stop_logs_capped(db, monkeypatch, caplog):
    served = _wire_real_client(monkeypatch, [
        _page([1, 2, 3], token="t2"),
        _page([4, 5, 6], token="t3"),
    ])
    con = storage.connect(db)
    try:
        caplog.set_level("INFO", logger="xbb")
        added = xapi.backfill_via_api(con, "cid", incremental=True, max_total=2)
        assert added == 2
        assert len(served) == 1  # never paid for a page past the entitlement
        assert "end_reason=capped" in caplog.text
    finally:
        con.close()


def test_incremental_caught_up_logs_caught_up(db, monkeypatch, caplog):
    _wire_real_client(monkeypatch, [_page([1, 2])])
    con = storage.connect(db)
    try:
        assert xapi.backfill_via_api(con, "cid", incremental=False) == 2
    finally:
        con.close()
    # Second, incremental run: first page is entirely known -> caught up, stop.
    served2 = _wire_real_client(monkeypatch, [_page([1, 2], token="t2")])
    con = storage.connect(db)
    try:
        caplog.set_level("INFO", logger="xbb")
        assert xapi.backfill_via_api(con, "cid", incremental=True) == 0
        assert len(served2) == 1
        assert "end_reason=caught_up" in caplog.text
    finally:
        con.close()
