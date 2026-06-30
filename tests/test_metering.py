"""Metering: usage_events records spend and usage_this_month sums it (RLS-scoped)."""

from xbb import storage, usage


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
