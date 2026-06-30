"""CLI admin actions.

    python -m xbb backfill     # pull your X bookmarks via the OAuth API (connect first in the web app)
    python -m xbb index        # embed bookmarks for semantic search (needs Bedrock)
    python -m xbb categorize   # derive a taxonomy (first run) + label bookmarks (needs Bedrock)

`index` and `categorize` are batched and resumable — safe to interrupt and re-run; they
only process rows that aren't done yet. `backfill` requires an X connection (do it once via
the web app's "Connect X" button — it stores the OAuth token this CLI reuses).
"""

from __future__ import annotations

import sys

from .config import Config


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
    if not cfg.x_client_id:
        print("X_CLIENT_ID is not set in .env", file=sys.stderr)
        return 2
    from . import xapi
    from .storage import connect, init_db

    init_db(cfg.db_path)
    con = connect(cfg.db_path)
    try:
        if not xapi.is_connected(con):
            print("Not connected to X. Start the web app and click 'Connect X' first "
                  "(http://127.0.0.1:8000).", file=sys.stderr)
            return 2
        print(f"Backfilling bookmarks into {cfg.db_path} via the X API ...")
        n = xapi.backfill_via_api(con, cfg.x_client_id, incremental=True)
        print(f"Done. {n} new bookmark(s) stored in {cfg.db_path}.")
    finally:
        con.close()
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
