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

    # Auth: secret for signing magic-link/session tokens; require_auth gates the app behind login
    # (off locally → no session falls back to the single default tenant; on for hosted/multi-user).
    session_secret: str
    require_auth: bool

    # Per-tenant monthly Bedrock spend cap (USD); None = unlimited. Enforced on /ask.
    # Fallback default — a subscribed account's own accounts.monthly_quota_usd takes precedence.
    monthly_quota_usd: float | None

    # Stripe (subscription billing)
    stripe_secret_key: str | None
    stripe_price_id: str | None
    stripe_webhook_secret: str | None
    # One-time prices for the credits model: ingestion charge + a credit pack.
    stripe_ingest_price_id: str | None
    stripe_credit_price_id: str | None

    # Credits model: flat price charged per ask; one-time ingestion price (display).
    ask_price_usd: float
    ingestion_price_usd: float

    # Free tier: bookmarks importable before paying (one-time slice) + free asks per day.
    free_bookmark_limit: int
    free_asks_per_day: int

    # Import slider: price per bookmark of purchased entitlement (first free_bookmark_limit free).
    price_per_bookmark_usd: float
    # Monthly credit subscription (grants pricing.SUB_MONTHLY_CREDITS_USD per invoice).
    stripe_credit_sub_price_id: str | None

    # AWS hardening: KMS key for encrypting X OAuth tokens; SES sender for magic-link emails.
    # Both optional — unset → tokens stored plaintext / magic link logged to console (local dev).
    kms_key_id: str | None
    ses_sender: str | None
    # Ops alerts (new signups, purchases) go here; unset = console-log only (local dev).
    owner_alert_email: str | None
    # The owner's own tenant gets deeper ask retrieval (k=50 vs 30) — a 17k corpus benefits
    # from a wider net; unset = nobody special.
    owner_tenant_id: str | None
    # Answer-model backend: "bedrock" (Claude via invoke_model) or "mantle" (Grok 4.3 via
    # the bedrock-mantle endpoint; needs BEDROCK_API_KEY). Mantle falls back to Claude on error.
    answer_backend: str
    bedrock_api_key: str | None
    # Model for ask answers (default: the reasoning model). Eval 2026-07-13: Haiku 4.5.
    answer_model: str | None
    # X Ads Conversion API (server-side registration attribution; see xconv.py). All six
    # must be set or tracking is skipped entirely (fail-safe).
    x_ads_pixel_id: str | None
    x_ads_event_id: str | None
    x_ads_consumer_key: str | None
    x_ads_consumer_secret: str | None
    x_ads_access_token: str | None
    x_ads_access_secret: str | None

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
            session_secret=os.getenv("SESSION_SECRET", "dev-insecure-secret-change-me"),
            require_auth=os.getenv("REQUIRE_AUTH", "").lower() in ("1", "true", "yes"),
            monthly_quota_usd=(
                float(os.environ["MONTHLY_QUOTA_USD"]) if os.getenv("MONTHLY_QUOTA_USD") else None
            ),
            stripe_secret_key=os.getenv("STRIPE_SECRET_KEY"),
            stripe_price_id=os.getenv("STRIPE_PRICE_ID"),
            stripe_webhook_secret=os.getenv("STRIPE_WEBHOOK_SECRET"),
            stripe_ingest_price_id=os.getenv("STRIPE_INGEST_PRICE_ID"),
            stripe_credit_price_id=os.getenv("STRIPE_CREDIT_PRICE_ID"),
            ask_price_usd=float(os.getenv("ASK_PRICE_USD", "0.05")),  # 2026-07-10 pivot: 10¢ -> 5¢
            ingestion_price_usd=float(os.getenv("INGESTION_PRICE_USD", "9.99")),
            free_bookmark_limit=int(os.getenv("FREE_BOOKMARK_LIMIT", "100")),
            free_asks_per_day=int(os.getenv("FREE_ASKS_PER_DAY", "5")),
            price_per_bookmark_usd=float(os.getenv("PRICE_PER_BOOKMARK_USD", "0.01")),
            stripe_credit_sub_price_id=os.getenv("STRIPE_CREDIT_SUB_PRICE_ID"),
            kms_key_id=os.getenv("KMS_KEY_ID"),
            ses_sender=os.getenv("SES_SENDER"),
            owner_alert_email=os.getenv("OWNER_ALERT_EMAIL"),
            owner_tenant_id=os.getenv("OWNER_TENANT_ID"),
            answer_backend=os.getenv("ANSWER_BACKEND", "bedrock"),
            bedrock_api_key=os.getenv("BEDROCK_API_KEY"),
            answer_model=os.getenv("ANSWER_MODEL"),
            x_ads_pixel_id=os.getenv("X_ADS_PIXEL_ID"),
            x_ads_event_id=os.getenv("X_ADS_EVENT_ID"),
            x_ads_consumer_key=os.getenv("X_ADS_CONSUMER_KEY"),
            x_ads_consumer_secret=os.getenv("X_ADS_CONSUMER_SECRET"),
            x_ads_access_token=os.getenv("X_ADS_ACCESS_TOKEN"),
            x_ads_access_secret=os.getenv("X_ADS_ACCESS_SECRET"),
        )
