"""Shared contracts and state helpers for external bookmark sources.

Adapters normalize source-specific records before handing them to the existing ingestion,
embedding, and categorization pipeline.  Only X is metered; adapters registered here are
non-X sources and therefore do not expose pricing or free-limit configuration.
"""

from __future__ import annotations

import json
import secrets
import time
from collections.abc import Callable, Iterable
from typing import Any, Protocol, runtime_checkable

from itsdangerous import BadData, URLSafeTimedSerializer

from . import crypto, storage
from .config import Config


@runtime_checkable
class SourceAdapter(Protocol):
    """The source-independent ingestion surface implemented by every adapter."""

    name: str

    def is_connected(self, con) -> bool: ...

    def backfill(
        self, con, cfg: Config, *, incremental: bool, max_total: int | None
    ) -> int: ...

    @staticmethod
    def record_id(native_id: str) -> str: ...


@runtime_checkable
class OAuthSourceAdapter(SourceAdapter, Protocol):
    """Additional operations required by an OAuth-backed source."""

    def is_configured(self, cfg: Config) -> bool: ...

    def authorize_url(self, cfg: Config, con, state: str) -> str: ...

    def handle_callback(self, cfg: Config, con, code: str, state: str) -> None: ...


REGISTRY: dict[str, SourceAdapter] = {}
_STATE_SALT = "xbb-source-oauth"
STATE_MAX_AGE_S = 900


class SourceError(ValueError):
    """Base error for invalid source registry requests."""


class UnknownSourceError(SourceError):
    """The requested source has no registered adapter."""


class SourceNotConfiguredError(SourceError):
    """The requested OAuth adapter is registered but lacks required configuration."""


def source_label(source: str) -> str:
    return {"github": "GitHub", "reddit": "Reddit", "x": "X"}.get(source, source.capitalize())


def register(adapter: SourceAdapter) -> SourceAdapter:
    """Register an adapter by its stable source name and return it for module-level use."""
    if not adapter.name:
        raise ValueError("source adapter name must not be empty")
    REGISTRY[adapter.name] = adapter
    return adapter


def get_adapter(source: str) -> SourceAdapter:
    """Return one registered adapter or raise the shared unknown-source error."""
    try:
        return REGISTRY[source]
    except KeyError as exc:
        raise UnknownSourceError(f"Unknown source: {source}") from exc


def get_oauth_adapter(
    source: str, cfg: Config, *, require_configured: bool = True
) -> OAuthSourceAdapter:
    """Return a typed OAuth adapter and optionally require its deploy-time settings."""
    adapter = get_adapter(source)
    if not isinstance(adapter, OAuthSourceAdapter):
        raise UnknownSourceError(f"Unknown OAuth source: {source}")
    if require_configured and not adapter.is_configured(cfg):
        raise SourceNotConfiguredError(f"{source_label(source)} OAuth is not configured.")
    return adapter


def get_configured_adapter(source: str, cfg: Config) -> SourceAdapter:
    """Return any adapter, enforcing configuration only when it is OAuth-backed."""
    adapter = get_adapter(source)
    if isinstance(adapter, OAuthSourceAdapter) and not adapter.is_configured(cfg):
        raise SourceNotConfiguredError(f"{source_label(source)} OAuth is not configured.")
    return adapter


def configured_oauth_sources(cfg: Config) -> list[OAuthSourceAdapter]:
    """Return registered OAuth adapters configured according to each adapter's own rules.

    This deliberately delegates configuration checks: Reddit needs only a client id, while
    GitHub needs both a client id and client secret.
    """
    return [
        adapter
        for adapter in REGISTRY.values()
        if isinstance(adapter, OAuthSourceAdapter) and adapter.is_configured(cfg)
    ]


def backfill_pages(
    con,
    source: str,
    pages: Iterable[list[dict[str, Any]]],
    parse: Callable[[dict[str, Any]], dict[str, Any] | None],
    *,
    incremental: bool,
    max_total: int | None,
) -> int:
    """Upsert newest-first provider pages with batched existence checks and shared ranking."""
    from .ingestion import _upsert_post

    before = con.execute("SELECT COUNT(*) FROM posts WHERE source = %s", (source,)).fetchone()[0]
    total = before
    new_ids: list[str] = []
    for page in pages:
        records = [record for item in page if (record := parse(item)) is not None]
        page_ids = [record["id"] for record in records]
        existing = set()
        if page_ids:
            existing = {
                row[0]
                for row in con.execute(
                    "SELECT id FROM posts WHERE id = ANY(%s)", (page_ids,)
                ).fetchall()
            }
        new_in_page = 0
        for record in records:
            post_id = record["id"]
            is_new = post_id not in existing
            if is_new and max_total is not None and total >= max_total:
                break
            _upsert_post(con, record)
            if is_new:
                existing.add(post_id)  # duplicate native records in one page count once
                total += 1
                new_in_page += 1
                new_ids.append(post_id)
        con.commit()
        if (max_total is not None and total >= max_total) or (incremental and new_in_page == 0):
            break
    _rank_new(con, new_ids)
    return con.execute("SELECT COUNT(*) FROM posts WHERE source = %s", (source,)).fetchone()[0] - before


def _rank_new(con, ids: list[str]) -> None:
    """Assign shared-library bookmark ranks to one newest-first batch."""
    if not ids:
        return
    base = con.execute("SELECT COALESCE(MAX(bm_rank), 0) FROM posts").fetchone()[0]
    params = [(base + i + 1, post_id) for i, post_id in enumerate(reversed(ids))]
    with con.cursor() as cursor:
        cursor.executemany("UPDATE posts SET bm_rank = %s WHERE id = %s", params)
    con.commit()


def record_id(prefix: str, native_id: str) -> str:
    """Namespace a provider id for the tenant-wide posts primary key.

    Prefixes are adapter-owned (for example ``reddit`` and ``gh``), allowing providers to
    retain their public id convention while avoiding raw X ids and ``web-`` browser ids.
    """
    if not prefix or not native_id:
        raise ValueError("record id prefix and native id must not be empty")
    return f"{prefix}-{native_id}"


def _enc_ctx(con) -> dict[str, str]:
    """Bind OAuth ciphertext to the current tenant's KMS encryption context."""
    row = con.execute("SELECT current_setting('app.current_tenant', true)").fetchone()
    return {"tenant_id": row[0]} if row and row[0] else {}


def _validate_token_key(key: str) -> None:
    if not key or not key.endswith("_oauth"):
        raise ValueError("OAuth token state keys must end with '_oauth'")


def save_tokens(
    con, key: str, tokens: dict[str, Any], *, cfg: Config | None = None
) -> None:
    """Encrypt and persist a provider token response under an explicit sync-state key.

    Expiring providers normally return ``expires_in``; convert that duration to an absolute
    timestamp while retaining provider-specific fields. Non-expiring tokens (such as GitHub
    OAuth App tokens) are stored without inventing an expiry.
    """
    _validate_token_key(key)
    rec = dict(tokens)
    if "expires_in" in rec and "expires_at" not in rec:
        rec["expires_at"] = time.time() + int(rec["expires_in"]) - 60
    active_cfg = cfg or Config.from_env()
    blob = crypto.encrypt(
        json.dumps(rec), active_cfg.kms_key_id, active_cfg.aws_region, _enc_ctx(con)
    )
    storage.set_state(con, key, blob)


def load_tokens(
    con, key: str, *, cfg: Config | None = None
) -> dict[str, Any] | None:
    """Load and decrypt one provider's token record for the current tenant."""
    _validate_token_key(key)
    raw = storage.get_state(con, key)
    if not raw:
        return None
    active_cfg = cfg or Config.from_env()
    return json.loads(crypto.decrypt(raw, active_cfg.aws_region, _enc_ctx(con)))


def is_connected(con, key: str, *, cfg: Config | None = None) -> bool:
    """Whether this tenant has a stored OAuth token for ``key``."""
    return load_tokens(con, key, cfg=cfg) is not None


def make_oauth_state(source: str, tenant_id: str, secret: str) -> str:
    """Sign source + tenant into a short-lived OAuth state token."""
    payload = {"source": source, "tenant_id": tenant_id, "nonce": secrets.token_urlsafe(18)}
    return URLSafeTimedSerializer(secret, salt=_STATE_SALT).dumps(payload)


def verify_oauth_state(
    state: str, source: str, tenant_id: str, secret: str, *, max_age_s: int = STATE_MAX_AGE_S
) -> bool:
    """Reject tampered, expired, cross-provider, and cross-tenant callbacks."""
    try:
        payload = URLSafeTimedSerializer(secret, salt=_STATE_SALT).loads(
            state, max_age=max_age_s
        )
    except BadData:
        return False
    return payload.get("source") == source and payload.get("tenant_id") == tenant_id


# Built-ins register themselves without performing network I/O.
from . import github as _github  # noqa: E402,F401
from . import reddit as _reddit  # noqa: E402,F401
