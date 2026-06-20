"""Runtime configuration, loaded from the environment (.env in local dev).

See .env.example for the full list. Nothing secret is hard-coded.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    # X session credentials for the one-time backfill
    x_auth_token: str | None
    x_csrf_token: str | None

    # AWS / Bedrock
    aws_region: str
    bedrock_labeling_model: str | None
    bedrock_reasoning_model: str | None
    bedrock_embedding_model: str | None

    # Local storage
    db_path: str

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            x_auth_token=os.getenv("X_AUTH_TOKEN"),
            x_csrf_token=os.getenv("X_CSRF_TOKEN"),
            aws_region=os.getenv("AWS_REGION", "us-east-1"),
            bedrock_labeling_model=os.getenv("BEDROCK_LABELING_MODEL"),
            bedrock_reasoning_model=os.getenv("BEDROCK_REASONING_MODEL"),
            bedrock_embedding_model=os.getenv("BEDROCK_EMBEDDING_MODEL"),
            db_path=os.getenv("XBB_DB_PATH", "data/xbb.db"),
        )
