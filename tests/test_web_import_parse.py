"""Pure tests for the browser-bookmark parser (bookmarks.py) — no DB, no AI, no network.

Both fixtures are realistic exports: Chrome and Firefox emit the same Netscape HTML, so one
parser covers both; the fixtures differ in the browser-specific noise they include
(bookmarklets/duplicates vs. place: smart folders / millisecond ADD_DATEs / unicode)."""

from pathlib import Path

from xbb.bookmarks import (
    compose_text,
    domain_of,
    parse_netscape_html,
    record_id,
    to_record,
    to_records,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_chrome_export_parses_links_and_folders():
    bms = parse_netscape_html(_load("chrome_bookmarks.html"))
    by_url = {b["url"]: b for b in bms}
    # 4 importable: bookmarklet skipped, duplicate URL collapsed to the first save
    assert len(bms) == 4
    assert "javascript:void(alert('hi'))" not in by_url

    asy = by_url["https://docs.python.org/3/library/asyncio.html"]
    assert asy["title"] == "asyncio — Asynchronous I/O"  # first save wins, entity decoded
    assert asy["folder"] == ""            # "Bookmarks bar" is filtered root noise
    assert asy["add_date"] == 1700000100

    pep = by_url["https://peps.python.org/pep-0008/"]
    assert pep["folder"] == "Dev/Python"  # nested folders, root container stripped

    recipe = by_url["https://example.com/recipe"]
    assert recipe["title"] == "Best pancake recipe & tips"
    assert recipe["folder"] == ""         # "Other bookmarks" is root noise too


def test_firefox_export_skips_smart_folders_and_scales_ms_dates():
    bms = parse_netscape_html(_load("firefox_bookmarks.html"))
    urls = [b["url"] for b in bms]
    assert not any(u.startswith("place:") for u in urls)  # Firefox smart folder skipped
    assert len(bms) == 2

    tira = next(b for b in bms if "tiramisu" in b["url"])
    assert tira["title"] == "Tiramisù — la ricetta perfetta ☕"
    assert tira["folder"] == "Ricette"    # "Bookmarks Toolbar" stripped
    assert tira["add_date"] == 1727000000  # ms ADD_DATE scaled to seconds


def test_record_id_is_deterministic_and_prefixed():
    a = record_id("https://example.com/x")
    assert a == record_id("https://example.com/x")
    assert a.startswith("web-") and len(a) == 20
    assert a != record_id("https://example.com/y")


def test_to_record_shape_matches_ingestion_contract():
    rec = to_record({"url": "https://example.com/a", "title": "A page",
                     "folder": "Dev", "add_date": 1700000000}, rank=7)
    assert rec["source"] == "browser"
    assert rec["author"] is None          # posts.author_id stays NULL (FK-safe)
    assert rec["kind"] == "original"
    assert rec["sort_index"] == 7
    assert rec["created_at"].startswith("2023-11-14")
    assert rec["text"] == "A page\nDev · example.com"
    assert rec["raw"]["title"] == "A page"


def test_to_records_ranks_oldest_first_from_base():
    bms = [{"url": f"https://e.com/{i}", "title": str(i), "folder": "", "add_date": d}
           for i, d in enumerate([300, 100, None, 200])]
    recs = to_records(bms, base_rank=50)
    # sorted by add_date ascending (None → oldest), ranks base+1.. ⇒ newest save ranks highest
    assert [(r["url"][-1], r["sort_index"]) for r in recs] == [
        ("2", 51), ("1", 52), ("3", 53), ("0", 54)]


def test_compose_text_has_v2_summary_slot():
    assert compose_text("T", "Dev", "e.com") == "T\nDev · e.com"
    assert compose_text(None, "", "e.com") == "e.com"          # untitled → domain stands in
    assert compose_text("T", None, "e.com", "About X.") == "T\ne.com\nAbout X."


def test_domain_of_strips_www_and_lowercases():
    assert domain_of("https://WWW.Example.COM/path?q=1") == "example.com"
