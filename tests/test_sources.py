"""Source adapter registry, OAuth state, configuration, and id namespacing."""

from dataclasses import replace

import pytest

from xbb import categorize, sources, storage
from xbb import ingestion
from xbb.config import Config
from xbb.templates import post_card


class FakeOAuthAdapter:
    def __init__(self, name: str, configured) -> None:
        self.name = name
        self._configured = configured

    def is_configured(self, cfg: Config) -> bool:
        return self._configured(cfg)

    def is_connected(self, con) -> bool:
        return sources.is_connected(con, f"{self.name}_oauth")

    def backfill(self, con, cfg, *, incremental, max_total):
        return 0

    @staticmethod
    def record_id(native_id: str) -> str:
        return sources.record_id("fake", native_id)

    def authorize_url(self, cfg, con, state):
        return "https://example.test/authorize"

    def handle_callback(self, cfg, con, code, state):
        return None


def test_record_id_namespaces_provider_ids():
    assert sources.record_id("reddit", "t3_abc") == "reddit-t3_abc"
    assert sources.record_id("gh", "123") == "gh-123"
    assert sources.record_id("reddit", "123") not in {"123", "web-123"}
    with pytest.raises(ValueError):
        sources.record_id("", "123")


def test_configured_oauth_sources_uses_adapter_specific_rules(monkeypatch):
    cfg = replace(
        Config.from_env(),
        reddit_client_id="reddit-id",
        github_client_id="github-id",
        github_client_secret=None,
    )
    reddit = FakeOAuthAdapter("reddit", lambda c: bool(c.reddit_client_id))
    github = FakeOAuthAdapter(
        "github", lambda c: bool(c.github_client_id and c.github_client_secret)
    )
    monkeypatch.setattr(sources, "REGISTRY", {"reddit": reddit, "github": github})

    assert sources.configured_oauth_sources(cfg) == [reddit]
    configured = replace(cfg, github_client_secret="github-secret")
    assert sources.configured_oauth_sources(configured) == [reddit, github]


def test_registry_accessors_share_unknown_and_configuration_errors(monkeypatch):
    cfg = replace(Config.from_env(), reddit_client_id=None)
    reddit = FakeOAuthAdapter("reddit", lambda c: bool(c.reddit_client_id))
    monkeypatch.setattr(sources, "REGISTRY", {"reddit": reddit})
    assert sources.get_adapter("reddit") is reddit
    with pytest.raises(sources.UnknownSourceError, match="Unknown source: absent"):
        sources.get_adapter("absent")
    with pytest.raises(sources.SourceNotConfiguredError, match="Reddit OAuth is not configured"):
        sources.get_oauth_adapter("reddit", cfg)


def test_oauth_config_defaults_and_environment(monkeypatch):
    for name in (
        "REDDIT_CLIENT_ID",
        "REDDIT_REDIRECT_URI",
        "GITHUB_CLIENT_ID",
        "GITHUB_CLIENT_SECRET",
        "GITHUB_REDIRECT_URI",
        "FREE_REDDIT_BOOKMARK_LIMIT",
        "FREE_GITHUB_BOOKMARK_LIMIT",
    ):
        monkeypatch.delenv(name, raising=False)
    cfg = Config.from_env()
    assert cfg.reddit_client_id is None
    assert cfg.reddit_redirect_uri.endswith("/connect/reddit/callback")
    assert cfg.github_client_id is None and cfg.github_client_secret is None
    assert cfg.github_redirect_uri.endswith("/connect/github/callback")
    assert not hasattr(cfg, "free_reddit_bookmark_limit")
    assert not hasattr(cfg, "free_github_bookmark_limit")
    assert not hasattr(cfg, "free_web_bookmark_limit")


def test_shared_oauth_token_store_round_trip_and_connection(monkeypatch):
    state = {}
    contexts = []

    class TenantConnection:
        def execute(self, sql):
            assert "current_setting" in sql
            return self

        def fetchone(self):
            return ("tenant-1",)

    def encrypt(value, key_id, region, context):
        contexts.append(context)
        return "encrypted:" + value

    def decrypt(value, region, context):
        contexts.append(context)
        return value.removeprefix("encrypted:")

    monkeypatch.setattr(sources.storage, "set_state", lambda con, key, value: state.__setitem__(key, value))
    monkeypatch.setattr(sources.storage, "get_state", lambda con, key: state.get(key))
    monkeypatch.setattr(sources.crypto, "encrypt", encrypt)
    monkeypatch.setattr(sources.crypto, "decrypt", decrypt)
    con = TenantConnection()

    assert sources.load_tokens(con, "reddit_oauth") is None
    assert sources.is_connected(con, "reddit_oauth") is False
    sources.save_tokens(
        con,
        "reddit_oauth",
        {"access_token": "access", "refresh_token": "refresh", "expires_in": 3600},
    )
    loaded = sources.load_tokens(con, "reddit_oauth")
    assert loaded["access_token"] == "access"
    assert loaded["refresh_token"] == "refresh"
    assert loaded["expires_at"] > 0
    assert sources.is_connected(con, "reddit_oauth") is True
    assert sources.load_tokens(con, "github_oauth") is None
    assert contexts == [{"tenant_id": "tenant-1"}] * 3


def test_feed_posts_returns_source(seeded_db, fake_ai):
    con = storage.connect(seeded_db)
    try:
        categorize.save_taxonomy(con, [{"name": "RAG"}, {"name": "Agents"}])
        categorize.assign_unassigned(con, fake_ai)
        posts = categorize.feed_posts(con)
        assert posts
        assert {post["source"] for post in posts} == {"x"}
    finally:
        con.close()


def test_signed_oauth_state_is_source_and_tenant_bound():
    state = sources.make_oauth_state("reddit", "tenant-a", "secret")
    assert sources.verify_oauth_state(state, "reddit", "tenant-a", "secret")
    assert not sources.verify_oauth_state(state, "github", "tenant-a", "secret")
    assert not sources.verify_oauth_state(state, "reddit", "tenant-b", "secret")
    assert not sources.verify_oauth_state(state + "tampered", "reddit", "tenant-a", "secret")


def test_post_card_renders_source_badges_and_author_links():
    reddit = post_card({"source": "reddit", "handle": "alice", "text": "saved"})
    github = post_card({"source": "github", "handle": "octo", "text": "repo"})
    browser = post_card({"source": "browser", "url": "https://www.example.com/a", "text": "web"})
    assert "👽 Reddit" in reddit and "https://www.reddit.com/user/alice" in reddit
    assert "🐙 GitHub" in github and "https://github.com/octo" in github
    assert "🌐 Web" in browser and "example.com" in browser


def test_post_card_rejects_unsafe_urls_and_encodes_author_handles():
    unsafe = post_card({"source": "browser", "url": "javascript:alert(1)", "text": "bad"})
    malformed = post_card({"source": "browser", "url": 123, "text": "bad"})
    author = post_card({"source": "reddit", "handle": "a/b ?", "text": "saved"})
    assert "javascript:" not in unsafe and "open ↗" not in unsafe
    assert "href=" not in malformed and "open ↗" not in malformed
    assert "https://www.reddit.com/user/a%2Fb%20%3F" in author
    assert "@a/b ?" in author


def test_shared_paged_backfill_batches_existing_id_lookup(monkeypatch):
    class Result:
        def __init__(self, rows):
            self.rows = rows

        def fetchone(self):
            return self.rows[0]

        def fetchall(self):
            return self.rows

    class Cursor:
        def __init__(self, con):
            self.con = con

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def executemany(self, sql, params):
            self.con.ranks.extend(params)

    class Connection:
        def __init__(self):
            self.ids = {"existing"}
            self.sql = []
            self.ranks = []

        def execute(self, sql, params=None):
            self.sql.append(sql)
            if "COUNT(*)" in sql:
                return Result([(len(self.ids),)])
            if "id = ANY" in sql:
                return Result([(post_id,) for post_id in params[0] if post_id in self.ids])
            if "MAX(bm_rank)" in sql:
                return Result([(10,)])
            raise AssertionError(sql)

        def cursor(self):
            return Cursor(self)

        def commit(self):
            pass

    con = Connection()
    monkeypatch.setattr(ingestion, "_upsert_post", lambda con, record: con.ids.add(record["id"]))
    page = [{"id": "new-1"}, {"id": "existing"}, {"id": "new-2"}]
    added = sources.backfill_pages(
        con, "fake", [page], lambda item: item, incremental=True, max_total=None
    )
    assert added == 2
    assert sum("id = ANY" in sql for sql in con.sql) == 1
    assert not any("WHERE id = %s" in sql for sql in con.sql)
    assert con.ranks == [(11, "new-2"), (12, "new-1")]
