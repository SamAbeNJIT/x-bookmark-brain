"""DB-backed PKCE state survives across instances and is single-use."""

from xbb import storage


def test_pkce_round_trip_and_single_use(db):
    con = storage.connect(db)
    try:
        storage.set_pkce(con, "state-abc", "verifier-xyz")
        assert storage.pop_pkce(con, "state-abc") == "verifier-xyz"
        assert storage.pop_pkce(con, "state-abc") is None  # consumed (one-time)
        assert storage.pop_pkce(con, "never-set") is None
    finally:
        con.close()
