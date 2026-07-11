"""RLS-safe tenant re-derive: wipe and rebuild ONE tenant's categorization.

THE ONLY sanctioned way to run tenant-data repairs. Born from the 2026-07-11 incident:
a repair connected with the OWNER role (which has BYPASSRLS), so its unqualified
DELETEs wiped every tenant's categories/assignments instead of one tenant's — recovered
via Neon point-in-time restore. Rule: tenant-data work connects as the RLS-enforced app
role (APP_DATABASE_URL), never the owner DSN (DATABASE_URL, DDL/migrations only).

Usage:
    python scripts/tenant_repair.py rederive <x_handle_or_email>
"""

from __future__ import annotations

import os
import sys

import psycopg

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from xbb import categorize, storage  # noqa: E402
from xbb.ai import BedrockAIClient  # noqa: E402


def _tenant_id(owner_dsn: str, who: str) -> str:
    with psycopg.connect(owner_dsn) as c:  # read-only lookup; accounts has no handle via RLS role
        row = c.execute(
            "SELECT id FROM accounts WHERE x_handle = %s OR email = %s", (who, who)
        ).fetchone()
    if not row:
        raise SystemExit(f"no account matches {who!r}")
    return str(row[0])


def _rls_guarded_connection(tenant_id: str) -> psycopg.Connection:
    """Connect as the app role and PROVE the connection cannot cross tenants."""
    con = storage.connect(os.environ["APP_DATABASE_URL"], tenant_id)
    user, bypass = con.execute(
        "SELECT current_user, rolbypassrls FROM pg_roles WHERE rolname = current_user"
    ).fetchone()
    if bypass:
        con.close()
        raise SystemExit(
            f"REFUSING: connected as {user}, which bypasses RLS — a scoping bug here "
            "would hit every tenant. Point APP_DATABASE_URL at the xbb_app role."
        )
    return con


def rederive(who: str) -> None:
    tid = _tenant_id(os.environ["DATABASE_URL"], who)
    con = _rls_guarded_connection(tid)
    try:
        visible = con.execute("SELECT count(*) FROM posts").fetchone()[0]
        print(f"tenant {tid} ({who}): {visible} posts visible under RLS")
        ai = BedrockAIClient(
            os.environ["AWS_REGION"],
            embedding_model=os.environ["BEDROCK_EMBEDDING_MODEL"],
            labeling_model=os.environ["BEDROCK_LABELING_MODEL"],
            reasoning_model=os.environ["BEDROCK_REASONING_MODEL"],
        )
        con.execute("DELETE FROM assignments")  # RLS-scoped to this tenant
        con.execute("DELETE FROM categories")
        con.execute("UPDATE posts SET label_attempted = NULL")
        con.commit()
        categorize.save_taxonomy(con, categorize.derive_taxonomy(con, ai))
        categorize.apply_default_parents(con)
        categorize.derive_parents(con, ai)
        n = categorize.assign_unassigned(con, ai)
        print(f"labeled {n} posts")
        for c in categorize.categories_with_counts(con):
            print(f"  {c['name']}: {c['count']}")
        print("unsorted:", categorize.unlabeled_count(con))
    finally:
        con.close()


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "rederive":
        raise SystemExit(__doc__)
    rederive(sys.argv[2])
