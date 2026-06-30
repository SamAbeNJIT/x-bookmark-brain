"""FastAPI dependencies — the wiring points the seams plug into.

Routes depend on `get_db` and `get_ai`; tests override these (via
`app.dependency_overrides`) to inject a temp database and a fake AI client, so the JSON
endpoints are testable without live X/AWS access.
"""

from __future__ import annotations

from fastapi import Request

from . import auth
from .ai import AIClient, BedrockAIClient
from .config import Config
from .storage import connect

SESSION_COOKIE = "xbb_session"


def get_config() -> Config:
    return Config.from_env()


def resolve_tenant(request: Request, cfg: Config) -> str:
    """The signed-in account's id (= its tenant), or the default single-user tenant if none."""
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        account_id = auth.verify_session_token(token, cfg.session_secret)
        if account_id:
            return account_id
    return cfg.tenant_id


def get_db(request: Request):
    # The web app connects as the restricted role so RLS is enforced (admin paths use the owner
    # DSN). The tenant is resolved per request from the session cookie.
    cfg = Config.from_env()
    con = connect(cfg.app_database_url, resolve_tenant(request, cfg))
    try:
        yield con
    finally:
        con.close()


def get_ai() -> AIClient:
    cfg = Config.from_env()
    return BedrockAIClient(
        region=cfg.aws_region,
        embedding_model=cfg.bedrock_embedding_model,
        labeling_model=cfg.bedrock_labeling_model,
        reasoning_model=cfg.bedrock_reasoning_model,
    )
