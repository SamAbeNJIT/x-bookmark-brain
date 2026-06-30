"""Runtime configuration, loaded from the environment (.env in local dev).

See .env.example for the full list. Nothing secret is hard-coded.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# Fixed tenant for the single-user/dev deployment (you = tenant #1). Multi-tenant auth
# (plan Inc 3) will resolve this per-request instead of from a constant.
DEFAULT_TENANT_ID = "00000000-0000-0000-0000-000000000001"


@dataclass(frozen=True)
class Config:
    # X OAuth 2.0 (PKCE public client) — the sanctioned bookmarks API path
    x_client_id: str | None
    x_redirect_uri: str

    # AWS / Bedrock
    aws_region: str
    bedrock_labeling_model: str | None
    bedrock_reasoning_model: str | None
    bedrock_embedding_model: str | None

    # Storage — Neon/Postgres DSNs + the active tenant.
    # database_url = owner (DDL/migrations/admin); app_database_url = restricted role the web app
    # connects as so RLS is enforced. Falls back to the owner DSN if the restricted role isn't set up.
    database_url: str | None
    app_database_url: str | None
    tenant_id: str

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            x_client_id=os.getenv("X_CLIENT_ID"),
            x_redirect_uri=os.getenv("X_REDIRECT_URI", "http://127.0.0.1:8000/oauth/callback"),
            aws_region=os.getenv("AWS_REGION", "us-east-1"),
            bedrock_labeling_model=os.getenv("BEDROCK_LABELING_MODEL"),
            bedrock_reasoning_model=os.getenv("BEDROCK_REASONING_MODEL"),
            bedrock_embedding_model=os.getenv("BEDROCK_EMBEDDING_MODEL"),
            database_url=os.getenv("DATABASE_URL"),
            app_database_url=os.getenv("APP_DATABASE_URL") or os.getenv("DATABASE_URL"),
            tenant_id=os.getenv("XBB_TENANT_ID", DEFAULT_TENANT_ID),
        )
