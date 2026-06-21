"""Resume-from-cursor behaviour for the backfill (filling a rate-limited gap)."""

from xbb.ingestion import run_backfill
from xbb.storage import connect, get_sync_cursor, init_db, set_sync_cursor

# A minimal raw payload parse_bookmark accepts (a tweet with a legacy block).
RAW = {
    "rest_id": "1",
    "legacy": {"full_text": "hello"},
    "core": {"user_results": {"result": {"rest_id": "u", "legacy": {"screen_name": "a", "name": "A"}}}},
}


class CursorClient:
    """Fake live client exposing `start_cursor`/`cursor` like GraphQLXClient.

    `final_cursor` is the value of `cursor` after the last page: None mimics reaching the
    end of the timeline; a string mimics being stopped partway (rate-limited).
    """

    def __init__(self, pages, final_cursor):
        self.pages = pages
        self.start_cursor = None
        self.cursor = None
        self._final = final_cursor

    def iter_bookmark_pages(self):
        n = len(self.pages)
        for i, page in enumerate(self.pages):
            self.cursor = self._final if i == n - 1 else f"c{i}"
            yield page


def test_cursor_cleared_when_backfill_completes(tmp_path):
    db = str(tmp_path / "x.db")
    run_backfill(CursorClient([[RAW]], final_cursor=None), db)
    con = connect(db)
    try:
        assert get_sync_cursor(con) is None  # finished → nothing to resume
    finally:
        con.close()


def test_cursor_saved_when_backfill_interrupted(tmp_path):
    db = str(tmp_path / "x.db")
    run_backfill(CursorClient([[RAW]], final_cursor="RESUME_HERE"), db)
    con = connect(db)
    try:
        assert get_sync_cursor(con) == "RESUME_HERE"  # stopped partway → resume point saved
    finally:
        con.close()


def test_resume_loads_saved_cursor_into_client(tmp_path):
    db = str(tmp_path / "x.db")
    init_db(db)
    con = connect(db)
    set_sync_cursor(con, "SAVED")
    con.close()

    client = CursorClient([[RAW]], final_cursor=None)
    run_backfill(client, db, resume=True)
    assert client.start_cursor == "SAVED"  # resumed from the saved cursor, not the top


def test_incremental_does_not_touch_resume_cursor(tmp_path):
    db = str(tmp_path / "x.db")
    init_db(db)
    con = connect(db)
    set_sync_cursor(con, "GAP_CURSOR")  # a pending gap-fill resume point
    con.close()

    # An incremental top-up must not overwrite the gap-fill cursor.
    run_backfill(CursorClient([[RAW]], final_cursor="c0"), db, incremental=True)
    con = connect(db)
    try:
        assert get_sync_cursor(con) == "GAP_CURSOR"
    finally:
        con.close()
