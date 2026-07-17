from urllib.parse import parse_qs, urlsplit

from conftest import load
from xbb import reddit, redditauth, sources, storage


def test_parse_saved_post_comment_and_deleted_author():
    children = load("reddit_saved.json")["data"]["children"]
    post, comment = map(reddit.parse_saved, children)
    assert post["id"] == "reddit-t3_abc" and post["source"] == "reddit"
    assert post["text"] == "A saved post\nUseful details"
    assert post["url"].startswith("https://www.reddit.com/r/python/")
    assert post["author"]["id"] == "reddit-user-alice"
    assert post["created_at"].startswith("2024-06-01T")
    assert comment["id"] == "reddit-t1_def"
    assert comment["title"] == "Discussion title" and comment["text"] == "A thoughtful comment"
    assert comment["author"] is None
    assert reddit.parse_saved({"kind": "t3", "data": {}}) is None


def test_authorize_url_is_permanent_pkce_history_read():
    query = parse_qs(urlsplit(redditauth.authorize_url("cid", "https://cb", "state", "challenge")).query)
    assert query["scope"] == ["read history"]
    assert query["duration"] == ["permanent"]
    assert query["code_challenge_method"] == ["S256"]


def test_token_exchange_uses_empty_secret_basic_auth_and_user_agent(monkeypatch):
    import httpx
    seen = {}

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"access_token": "token"}

    def post(url, **kwargs):
        seen.update(url=url, **kwargs)
        return Response()

    monkeypatch.setattr(httpx, "post", post)
    assert redditauth.exchange_code("cid", "https://cb", "code", "verifier")["access_token"] == "token"
    assert seen["auth"] == ("cid", "")
    assert seen["headers"]["User-Agent"] == redditauth.USER_AGENT
    assert seen["data"]["code_verifier"] == "verifier"


class FakeRedditClient:
    def __init__(self, pages):
        self.pages = pages

    def iter_saved_pages(self, username):
        assert username == "tester"
        yield from self.pages


def test_backfill_is_ranked_incremental_and_idempotent(db, monkeypatch):
    monkeypatch.setenv("REDDIT_CLIENT_ID", "cid")
    con = storage.connect(db)
    try:
        sources.save_tokens(con, reddit.TOKEN_KEY,
                            {"access_token": "a", "refresh_token": "r", "expires_in": 3600,
                             "username": "tester"})
        page = load("reddit_saved.json")["data"]["children"]
        cfg = __import__("xbb.config", fromlist=["Config"]).Config.from_env()
        assert reddit.backfill(con, cfg, incremental=True, max_total=None,
                               client=FakeRedditClient([page])) == 2
        ranks = con.execute("SELECT id, bm_rank FROM posts ORDER BY bm_rank DESC").fetchall()
        assert [row[0] for row in ranks] == ["reddit-t3_abc", "reddit-t1_def"]
        assert reddit.backfill(con, cfg, incremental=True, max_total=None,
                               client=FakeRedditClient([page, [{"unexpected": True}]])) == 0
        assert storage.post_count(con, "reddit") == 2
    finally:
        con.close()


def test_saved_listing_stops_at_reddit_1000_cap():
    client = object.__new__(reddit.RedditApiClient)
    calls = 0

    def get(path, params):
        nonlocal calls
        calls += 1
        start = (calls - 1) * 100
        return {"data": {"children": [{"kind": "t3", "data": {"name": f"t3_{i}"}}
                                       for i in range(start, start + 100)],
                         "after": f"t3_{start + 99}"}}

    client._get = get
    assert sum(len(page) for page in client.iter_saved_pages("tester")) == 1000
    assert calls == 10
