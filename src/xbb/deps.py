"""FastAPI dependencies — the wiring points the seams plug into.

Routes depend on `get_db` and `get_ai`; tests override these (via
`app.dependency_overrides`) to inject a temp database and a fake AI client, so the JSON
endpoints are testable without live X/AWS access.
"""

from __future__ import annotations

from .ai import AIClient, BedrockAIClient
from .config import Config
from .storage import connect


def get_config() -> Config:
    return Config.from_env()


def get_db():
    # The web app connects as the restricted role so RLS is enforced (admin paths use the owner
    # DSN). tenant_id is the single default today; auth will resolve it per request.
    cfg = Config.from_env()
    con = connect(cfg.app_database_url, cfg.tenant_id)
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
