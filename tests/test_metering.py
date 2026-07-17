"""Metering: usage_events records spend and usage_this_month sums it (RLS-scoped)."""

from xbb import storage, usage
from xbb.config import DEFAULT_TENANT_ID


def test_records_and_sums_usage(db):
    con = storage.connect(db)
    try:
        haiku = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
        titan = "amazon.titan-embed-text-v2:0"
        storage.record_usage(con, haiku, 1000, 200, usage.cost_of(haiku, 1000, 200))
        storage.record_usage(con, titan, 5000, 0, usage.cost_of(titan, 5000, 0))
        total = storage.usage_this_month(con)
        # haiku: 1000*$1/1M + 200*$5/1M = 0.002 ; titan: 5000*$0.02/1M = 0.0001
        assert abs(total - 0.0021) < 1e-9
    finally:
        con.close()


def test_quota_predicate_with_real_usage(db):
    con = storage.connect(db)
    try:
        haiku = "us.anthropic.claude-haiku-4-5"
        storage.record_usage(con, haiku, 1_000_000, 0, usage.cost_of(haiku, 1_000_000, 0))  # $1.00
        used = storage.usage_this_month(con)
        assert usage.within_quota(used, 5.0) is True     # under a $5 cap
        assert usage.within_quota(used, 0.5) is False    # over a $0.50 cap
    finally:
        con.close()


def test_non_x_posts_never_consume_x_import_pool(db):
    """Browser overage is free: only stored X posts draw from purchased imports."""
    con = storage.connect(db)
    try:
        con.execute(
            "UPDATE accounts SET ingestion_paid = false, import_limit = 3 WHERE id = %s",
            (DEFAULT_TENANT_ID,),
        )
        con.execute(
            "INSERT INTO posts (id, source, text) "
            "SELECT 'x-' || n, 'x', 'x post' FROM generate_series(1, 101) n"
        )
        con.commit()
        assert storage.effective_import_cap(con, 100) == 103
        assert storage.imports_available(con, 100) == 2

        con.execute(
            "INSERT INTO posts (id, source, text) "
            "SELECT 'web-' || n, 'browser', 'browser post' FROM generate_series(1, 1000) n"
        )
        con.commit()
        assert storage.post_count(con, "browser") == 1000
        assert storage.effective_import_cap(con, 100) == 103
        assert storage.imports_available(con, 100) == 2
    finally:
        con.close()


def test_x_only_import_math_ignores_browser_overage(monkeypatch):
    """Exercise the metering seam without a database: browser count is never consulted."""
    calls = []
    monkeypatch.setattr(storage, "is_ingestion_paid", lambda con: False)
    monkeypatch.setattr(storage, "import_limit", lambda con: 3)

    def count(con, source):
        calls.append(source)
        return {"x": 101, "browser": 10_000}[source]

    monkeypatch.setattr(storage, "post_count", count)
    assert storage.effective_import_cap(object(), 100) == 103
    assert storage.imports_available(object(), 100) == 2
    assert calls == ["x"]
