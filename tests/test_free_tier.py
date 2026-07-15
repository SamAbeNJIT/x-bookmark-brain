"""Freemium: daily free asks (used before credits) and the free bookmark-import slice."""

from xbb import storage, xapi
from xbb.config import DEFAULT_TENANT_ID


def test_free_ask_allowance_grants_then_denies(db):
    con = storage.connect(db)
    try:
        assert storage.free_asks_used_today(con) == 0
        assert storage.use_free_ask(con, 2) is True
        assert storage.use_free_ask(con, 2) is True
        assert storage.use_free_ask(con, 2) is False   # limit reached
        assert storage.free_asks_used_today(con) == 2  # never exceeds the limit
    finally:
        con.close()


def test_ask_uses_free_allowance_before_credits(client, db):
    # Balance 0 but free asks remain -> the ask succeeds without any debit.
    con = storage.connect(db)
    con.execute("UPDATE accounts SET credit_balance_usd = 0 WHERE id = %s", (DEFAULT_TENANT_ID,))
    con.commit(); con.close()
    body = client.post("/ask", json={"question": "rag evaluation", "k": 3}).json()
    assert "credit balance is empty" not in body["answer"].lower()  # answered for free
    con = storage.connect(db)
    try:
        assert storage.free_asks_used_today(con) == 1
        assert storage.credit_balance(con) == 0.0  # no debit
    finally:
        con.close()


class _FakePagedClient:
    """Stands in for XApiClient: yields v2-shaped pages of 10 tweets each, counting requests
    (every page served = billed X-API reads, so overshooting pages costs real money)."""

    pages_served = 0

    def __init__(self, con, client_id):
        self._pages = [
            {"data": [{"id": str(p * 10 + i), "text": f"post {p * 10 + i}"} for i in range(10)],
             "includes": {}}
            for p in range(5)  # 50 tweets total
        ]

    def iter_bookmark_pages(self, max_results=100):
        for page in self._pages:
            _FakePagedClient.pages_served += 1
            yield page


def test_capped_backfill_requests_small_pages(db, monkeypatch):
    """X bills per post RETURNED: a 25-cap fetch must request 26 (cap + 1 proof-of-more
    post), never a full 100-page (4x the spend for the same free slice)."""
    seen_sizes = []

    class _Recorder:
        def __init__(self, con, client_id):
            pass

        def iter_bookmark_pages(self, max_results=100):
            seen_sizes.append(max_results)
            yield {"data": [{"id": str(i), "text": f"p{i}"} for i in range(max_results)],
                   "includes": {}}

    monkeypatch.setattr(xapi, "XApiClient", _Recorder)
    con = storage.connect(db)
    try:
        xapi.backfill_via_api(con, "cid", incremental=True, max_total=25)
        assert seen_sizes == [26]  # cap + 1, exactly one page requested
        assert con.execute("SELECT COUNT(*) FROM posts").fetchone()[0] == 25
        assert storage.library_more_exists(con) is True  # the +1 proved more exist
    finally:
        con.close()


def test_backfill_caps_at_free_limit(db, monkeypatch):
    monkeypatch.setattr(xapi, "XApiClient", _FakePagedClient)
    _FakePagedClient.pages_served = 0
    con = storage.connect(db)
    try:
        added = xapi.backfill_via_api(con, "cid", incremental=True, max_total=25)
        assert added == 25  # stopped mid-timeline at the free slice
        assert con.execute("SELECT COUNT(*) FROM posts").fetchone()[0] == 25
        # Cost guarantee: never request a page past the entitlement (25 needs exactly 3 pages
        # of 10 — a 4th request would be pure wasted X-API spend).
        assert _FakePagedClient.pages_served == 3
    finally:
        con.close()


def test_full_import_after_upgrade_reranks_correctly(db, monkeypatch):
    monkeypatch.setattr(xapi, "XApiClient", _FakePagedClient)
    con = storage.connect(db)
    try:
        xapi.backfill_via_api(con, "cid", incremental=True, max_total=25)   # free slice
        added = xapi.backfill_via_api(con, "cid", incremental=False)        # post-upgrade full run
        assert added == 25  # the remaining 25
        assert con.execute("SELECT COUNT(*) FROM posts").fetchone()[0] == 50
        # Ordering: the first-fetched (newest) post must hold the highest bm_rank even though
        # the older half was stored in a later run.
        top = con.execute("SELECT id FROM posts ORDER BY bm_rank DESC LIMIT 1").fetchone()[0]
        assert top == "0"  # first tweet of page 0 = newest
    finally:
        con.close()
