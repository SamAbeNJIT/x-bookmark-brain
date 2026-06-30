"""One-off: create the restricted app role (xbb_app) so RLS is actually enforced.

The Neon owner has BYPASSRLS, so the app must connect as a separate role that does NOT. This
script creates that role (LOGIN, no BYPASSRLS), applies grants in neondb + neondb_test (via
init_db), and writes APP_DATABASE_URL into .env. Run once:

    .venv/bin/python scripts/setup_app_role.py
"""

from __future__ import annotations

import re
import secrets
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from psycopg import sql

from xbb import storage
from xbb.config import DEFAULT_TENANT_ID

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


def _app_dsn(owner_dsn: str, password: str, dbname: str | None = None) -> str:
    prefix, rest = owner_dsn.split("://", 1)
    _userinfo, hostpart = rest.split("@", 1)
    dsn = f"{prefix}://{storage.APP_ROLE}:{password}@{hostpart}"
    if dbname:
        dsn = re.sub(r"/[^/?]+\?", f"/{dbname}?", dsn, count=1)
    return dsn


def _write_env(key: str, value: str) -> None:
    lines = ENV_PATH.read_text().splitlines() if ENV_PATH.exists() else []
    lines = [ln for ln in lines if not ln.startswith(f"{key}=")]
    lines.append(f"{key}={value}")
    ENV_PATH.write_text("\n".join(lines) + "\n")


def main() -> int:
    load_dotenv()
    import os
    owner = os.environ["DATABASE_URL"]
    test_owner = re.sub(r"/[^/?]+\?", "/neondb_test?", owner, count=1)
    password = secrets.token_urlsafe(24)

    with psycopg.connect(owner, autocommit=True) as con:
        exists = con.execute(
            "SELECT 1 FROM pg_roles WHERE rolname = %s", (storage.APP_ROLE,)
        ).fetchone()
        verb = "ALTER" if exists else "CREATE"
        con.execute(
            sql.SQL("{} ROLE {} WITH LOGIN PASSWORD {}").format(
                sql.SQL(verb), sql.Identifier(storage.APP_ROLE), sql.Literal(password)
            )
        )
        print(f"{verb.lower()}d role {storage.APP_ROLE} (LOGIN, no BYPASSRLS)")

    # Apply grants in both databases (init_db's _apply_grants now sees the role).
    storage.init_db(owner, DEFAULT_TENANT_ID)
    storage.init_db(test_owner, DEFAULT_TENANT_ID)
    print("grants applied in neondb + neondb_test")

    app_dsn = _app_dsn(owner, password)
    _write_env("APP_DATABASE_URL", app_dsn)
    print(f"wrote APP_DATABASE_URL to {ENV_PATH} (role={storage.APP_ROLE})")

    # Sanity: connect as the app role and confirm it does NOT bypass RLS.
    with storage.connect(app_dsn, DEFAULT_TENANT_ID) as c:
        bypass = c.execute(
            "SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user"
        ).fetchone()[0]
        print(f"app role connects OK; bypassrls={bypass} (must be False for enforcement)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
