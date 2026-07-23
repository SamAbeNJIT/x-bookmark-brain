"""Tenant isolation: prove Row-Level Security actually keeps tenants' data separate.

The single most valuable safety net for the pool (shared-DB) model: connect as the restricted
app role (no BYPASSRLS) and assert one tenant can never see, or write as, another. If RLS were
misconfigured these would fail loudly.
"""

import psycopg
import pytest

from xbb import storage
from xbb.config import DEFAULT_TENANT_ID

TENANT_A = DEFAULT_TENANT_ID
TENANT_B = "00000000-0000-0000-0000-0000000000b2"


def _seed_two_tenants(owner_dsn: str) -> None:
    """As the owner, drop one post into each tenant (owner bypasses RLS, so it can seed both)."""
    con = storage.connect(owner_dsn, TENANT_A)
    try:
        con.execute("INSERT INTO posts (id, text) VALUES ('a1', 'tenant A post')")
        con.execute("SELECT set_config('app.current_tenant', %s, false)", (TENANT_B,))
        con.execute("INSERT INTO posts (id, text) VALUES ('b1', 'tenant B post')")
        con.commit()
    finally:
        con.close()


def test_rls_scopes_reads_to_the_current_tenant(db, app_db):
    _seed_two_tenants(db)

    a = storage.connect(app_db, TENANT_A)
    try:
        # An unscoped query still returns ONLY tenant A's rows (RLS appends the filter).
        ids = {r[0] for r in a.execute("SELECT id FROM posts")}
        assert ids == {"a1"}
        # Tenant B's row is invisible — 0 rows, not an error.
        assert a.execute("SELECT 1 FROM posts WHERE id = 'b1'").fetchone() is None
    finally:
        a.close()

    b = storage.connect(app_db, TENANT_B)
    try:
        assert {r[0] for r in b.execute("SELECT id FROM posts")} == {"b1"}
    finally:
        b.close()


def test_rls_blocks_writing_as_another_tenant(db, app_db):
    _seed_two_tenants(db)
    a = storage.connect(app_db, TENANT_A)
    try:
        # Stamping a row with someone else's tenant_id violates the policy's WITH CHECK.
        with pytest.raises(psycopg.errors.Error):
            a.execute(
                "INSERT INTO posts (tenant_id, id, text) VALUES (%s, 'x', 'sneaky')",
                (TENANT_B,),
            )
            a.commit()
    finally:
        a.close()


def test_state_claims_are_independent_per_tenant(db, app_db):
    """The same F1 key can be claimed once by each tenant, never globally."""
    owner = storage.connect(db, TENANT_A)
    try:
        owner.execute("INSERT INTO accounts (id) VALUES (%s) ON CONFLICT (id) DO NOTHING",
                      (TENANT_B,))
        owner.commit()
    finally:
        owner.close()
    a = storage.connect(app_db, TENANT_A)
    b = storage.connect(app_db, TENANT_B)
    try:
        assert storage.claim_state(a, "auto_answer:v1", "tenant-a")
        assert not storage.claim_state(a, "auto_answer:v1", "duplicate")
        assert storage.claim_state(b, "auto_answer:v1", "tenant-b")
        assert storage.get_state(a, "auto_answer:v1") == "tenant-a"
        assert storage.get_state(b, "auto_answer:v1") == "tenant-b"
    finally:
        a.close()
        b.close()
