from urllib.parse import parse_qs, urlsplit

from conftest import load
from xbb import github, githubauth, sources, storage


def test_parse_star_maps_repo_and_is_null_safe():
    first, null_safe = load("github_starred.json")
    parsed = github.parse_star(first)
    assert parsed["id"] == "gh-101" and parsed["source"] == "github"
    assert parsed["author"]["id"] == "gh-user-9" and parsed["author"]["handle"] == "acme"
    assert all(value in parsed["text"] for value in ("acme/alpha", "Alpha toolkit", "python rag"))
    assert parsed["bookmarked_at"] == first["starred_at"]
    assert github.parse_star(null_safe)["text"] == "octo/null-safe\n\n"


def test_github_client_follows_link_next_pages():
    client = object.__new__(github.GitHubApiClient)
    calls = []
    pages = {
        f"{github.API}/user/starred?per_page=100": ([{"repo": {"id": 1}}], "https://next"),
        "https://next": ([{"repo": {"id": 2}}], None),
    }
    client._get = lambda url: calls.append(url) or pages[url]
    assert list(client.iter_starred_pages()) == [[{"repo": {"id": 1}}], [{"repo": {"id": 2}}]]
    assert calls == [f"{github.API}/user/starred?per_page=100", "https://next"]


def test_github_client_retries_rate_limit(monkeypatch):
    import httpx

    class Response:
        def __init__(self, status, headers=None):
            self.status_code = status
            self.headers = headers or {}
            self.links = {}

        def raise_for_status(self):
            assert self.status_code == 200

        def json(self):
            return [{"repo": {"id": 1}}]

    responses = [Response(429, {"retry-after": "3"}), Response(200)]
    sleeps = []
    monkeypatch.setattr(httpx, "get", lambda *args, **kwargs: responses.pop(0))
    monkeypatch.setattr(github.time, "sleep", sleeps.append)
    client = object.__new__(github.GitHubApiClient)
    client.access_token = "token"
    assert client._get("https://api.github.test/starred")[0] == [{"repo": {"id": 1}}]
    assert sleeps == [3.0]


def test_github_authorize_url_has_empty_public_scope_and_state():
    query = parse_qs(urlsplit(githubauth.authorize_url("cid", "https://cb", "signed")).query,
                     keep_blank_values=True)
    assert query["client_id"] == ["cid"] and query["state"] == ["signed"]
    assert query["scope"] == [""]


def test_exchange_code_posts_confidential_secret_as_json_response(monkeypatch):
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
    token = githubauth.exchange_code("cid", "secret", "https://cb", "code", "state")
    assert token == {"access_token": "token"}
    assert seen["data"]["client_secret"] == "secret"
    assert seen["headers"]["Accept"] == "application/json"


class FakeGitHubClient:
    def __init__(self, pages):
        self.pages = pages

    def iter_starred_pages(self):
        yield from self.pages


def test_github_backfill_pages_and_is_idempotent(db, monkeypatch):
    monkeypatch.setenv("GITHUB_CLIENT_ID", "cid")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "secret")
    con = storage.connect(db)
    try:
        sources.save_tokens(con, github.TOKEN_KEY, {"access_token": "token"})
        stars = load("github_starred.json")
        cfg = __import__("xbb.config", fromlist=["Config"]).Config.from_env()
        assert github.backfill(con, cfg, incremental=True, max_total=None,
                               client=FakeGitHubClient([[stars[0]], [stars[1]]])) == 2
        assert storage.post_count(con, "github") == 2
        assert github.backfill(con, cfg, incremental=True, max_total=None,
                               client=FakeGitHubClient([stars])) == 0
    finally:
        con.close()
