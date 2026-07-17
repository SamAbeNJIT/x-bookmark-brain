"""CLI admin actions.

    python -m xbb backfill [--source x|reddit|github]
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


def _backfill(source: str = "x") -> int:
    _load_env()
    cfg = Config.from_env()
    if source == "x" and not cfg.x_client_id:
        print("X_CLIENT_ID is not set in .env", file=sys.stderr)
        return 2
    if not cfg.database_url:
        print("DATABASE_URL is not set in .env", file=sys.stderr)
        return 2
    from . import sources, xapi
    from .storage import connect, init_db

    init_db(cfg.database_url, cfg.tenant_id)
    con = connect(cfg.database_url, cfg.tenant_id)
    try:
        if source == "x":
            connected = xapi.is_connected(con)
        else:
            try:
                adapter = sources.get_configured_adapter(source, cfg)
            except sources.SourceError as exc:
                print(str(exc), file=sys.stderr)
                return 2
            connected = adapter.is_connected(con)
        if not connected:
            print(f"Not connected to {sources.source_label(source)}. Connect it in the web app first.",
                  file=sys.stderr)
            return 2
        print(f"Backfilling saved items into the database via {sources.source_label(source)} ...")
        n = (xapi.backfill_via_api(con, cfg.x_client_id, incremental=True)
             if source == "x" else
             adapter.backfill(con, cfg, incremental=True, max_total=None))
        print(f"Done. {n} new bookmark(s) stored.")
    finally:
        con.close()
    return 0


def _index() -> int:
    _load_env()
    cfg = Config.from_env()
    from .search import index_posts
    from .storage import connect

    con = connect(cfg.database_url, cfg.tenant_id)
    try:
        print("Embedding bookmarks (resumable) ...")
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

    con = connect(cfg.database_url, cfg.tenant_id)
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
    if argv[0] == "backfill":
        source = "x"
        if len(argv) > 1:
            if len(argv) != 3 or argv[1] != "--source":
                print("Usage: python -m xbb backfill [--source x|reddit|github]", file=sys.stderr)
                return 2
            source = argv[2].lower()
        return _backfill(source)
    return command()


if __name__ == "__main__":
    raise SystemExit(main())
