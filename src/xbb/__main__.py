"""CLI admin actions.

    python -m xbb backfill            # pull your X bookmarks into the local DB (needs X cookies)
    python -m xbb backfill --resume   # continue a backfill X rate-limited (from the saved cursor)
    python -m xbb index        # embed bookmarks for semantic search (needs Bedrock)
    python -m xbb categorize   # derive a taxonomy (first run) + label bookmarks (needs Bedrock)

`index` and `categorize` are batched and resumable — safe to interrupt and re-run; they
only process rows that aren't done yet.
"""

from __future__ import annotations

import os
import sys

from .config import Config
from .ingestion import DEFAULT_QUERY_ID, GraphQLXClient, run_backfill


def _load_env() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass


def _ai(cfg: Config):
    from .ai import BedrockAIClient

    return BedrockAIClient(
        region=cfg.aws_region,
        embedding_model=cfg.bedrock_embedding_model,
        labeling_model=cfg.bedrock_labeling_model,
        reasoning_model=cfg.bedrock_reasoning_model,
    )


def _progress(done: int, total: int) -> None:
    print(f"  {done}/{total}", end="\r", flush=True)
    if done >= total:
        print()


def _backfill() -> int:
    _load_env()
    cfg = Config.from_env()
    if not cfg.x_auth_token or not cfg.x_csrf_token:
        print("Missing X_AUTH_TOKEN / X_CSRF_TOKEN in .env", file=sys.stderr)
        return 2
    client = GraphQLXClient(
        auth_token=cfg.x_auth_token,
        csrf_token=cfg.x_csrf_token,
        query_id=os.getenv("X_BOOKMARKS_QUERY_ID", DEFAULT_QUERY_ID),
    )
    resume = "--resume" in sys.argv
    where = "resuming from the saved cursor" if resume else "from the top"
    print(f"Backfilling bookmarks into {cfg.db_path} ({where}) ...")
    n = run_backfill(client, cfg.db_path, resume=resume)
    print(f"Done. {n} bookmarks fetched this run (stored in {cfg.db_path}).")
    print("If X rate-limited mid-run, wait ~15 min and re-run with --resume to continue.")
    return 0


def _index() -> int:
    _load_env()
    cfg = Config.from_env()
    from .search import index_posts
    from .storage import connect

    con = connect(cfg.db_path)
    try:
        print(f"Embedding bookmarks in {cfg.db_path} (resumable) ...")
        n = index_posts(con, _ai(cfg), progress=_progress)
    finally:
        con.close()
    print(f"Done. Embedded {n} new bookmarks.")
    return 0


def _categorize() -> int:
    _load_env()
    cfg = Config.from_env()
    from . import categorize
    from .storage import connect

    con = connect(cfg.db_path)
    try:
        ai = _ai(cfg)
        if not categorize.get_taxonomy(con):
            print("No taxonomy yet — deriving one from your bookmarks ...")
            proposed = categorize.derive_taxonomy(con, ai)
            categorize.save_taxonomy(con, proposed)
            print(f"  proposed {len(proposed)} categories (refine them on the taxonomy page).")
        categorize.apply_default_parents(con)  # group categories for the tree view
        print("Assigning categories (resumable) ...")
        n = categorize.assign_unassigned(con, ai, progress=_progress)
    finally:
        con.close()
    print(f"Done. Categorized {n} bookmarks.")
    return 0


_COMMANDS = {"backfill": _backfill, "index": _index, "categorize": _categorize}


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if not argv or argv[0] in {"-h", "--help"}:
        print(__doc__)
        return 0
    command = _COMMANDS.get(argv[0])
    if command is None:
        print(f"Unknown command: {argv[0]}\n", file=sys.stderr)
        print(__doc__, file=sys.stderr)
        return 2
    return command()


if __name__ == "__main__":
    raise SystemExit(main())
