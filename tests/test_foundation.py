"""Foundation slice (issue #1): app boots, config loads, storage initializes.

Tests assert external behavior — the health endpoint, config values, and the schema /
idempotency guarantees — not implementation details.
"""

from fastapi.testclient import TestClient

from xbb.config import Config
from xbb.storage import connect, init_db
from xbb.web import create_app

EXPECTED_TABLES = {
    "authors",
    "posts",
    "self_thread_posts",
    "categories",
    "assignments",
    "embeddings",
}


def test_health_endpoint_returns_ok():
    client = TestClient(create_app())
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_config_reads_from_env(monkeypatch):
    monkeypatch.setenv("X_CLIENT_ID", "cid")
    monkeypatch.setenv("AWS_REGION", "eu-west-1")
    monkeypatch.setenv("BEDROCK_EMBEDDING_MODEL", "amazon.titan-embed-text-v2:0")
    cfg = Config.from_env()
    assert cfg.x_client_id == "cid"
    assert cfg.aws_region == "eu-west-1"
    assert cfg.bedrock_embedding_model == "amazon.titan-embed-text-v2:0"


def test_init_db_creates_all_tables(db):
    init_db(db)
    con = connect(db)
    try:
        tables = {
            r[0] for r in con.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
            )
        }
    finally:
        con.close()
    assert EXPECTED_TABLES <= tables


def test_init_db_is_idempotent(db):
    init_db(db)
    init_db(db)  # must not raise on a second run


def test_posts_keyed_by_id_so_upserts_are_idempotent(db):
    init_db(db)
    con = connect(db)
    try:
        con.execute("INSERT INTO posts (id, text) VALUES ('123', 'first')")
        con.execute(
            "INSERT INTO posts (id, text) VALUES ('123', 'second') "
            "ON CONFLICT (tenant_id, id) DO UPDATE SET text = excluded.text"
        )
        con.commit()
        count = con.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
        text = con.execute("SELECT text FROM posts WHERE id = '123'").fetchone()[0]
    finally:
        con.close()
    assert count == 1
    assert text == "second"
