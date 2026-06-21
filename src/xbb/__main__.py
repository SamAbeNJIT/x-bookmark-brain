"""CLI admin actions. Usage: python -m xbb backfill"""

from __future__ import annotations

import sys

from .config import Config
from .ingestion import DEFAULT_QUERY_ID, GraphQLXClient, run_backfill


def _backfill() -> int:
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

    import os

    cfg = Config.from_env()
    if not cfg.x_auth_token or not cfg.x_csrf_token:
        print("Missing X_AUTH_TOKEN / X_CSRF_TOKEN in .env", file=sys.stderr)
        return 2

    client = GraphQLXClient(
        auth_token=cfg.x_auth_token,
        csrf_token=cfg.x_csrf_token,
        query_id=os.getenv("X_BOOKMARKS_QUERY_ID", DEFAULT_QUERY_ID),
    )
    print(f"Backfilling bookmarks into {cfg.db_path} ...")
    n = run_backfill(client, cfg.db_path)
    print(f"Done. {n} bookmarks stored/updated in {cfg.db_path}.")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if not argv or argv[0] in {"-h", "--help"}:
        print("Usage: python -m xbb backfill")
        return 0
    if argv[0] == "backfill":
        return _backfill()
    print(f"Unknown command: {argv[0]}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
