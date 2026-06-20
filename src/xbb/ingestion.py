"""Ingestion seam: pull the user's bookmarks from X and parse them into records.

`XClient` is the single seam wrapping X's internal GraphQL bookmarks endpoint. Tests feed
recorded sample payloads (a reply, a quote, a self-thread) to `parse_bookmark` and assert
the records produced — no live X calls in tests (see docs/PRD.md → Testing Decisions).
"""

from __future__ import annotations

from typing import Any, Iterator, Protocol


class XClient(Protocol):
    """Wraps authenticated access to X's internal bookmarks endpoint."""

    def iter_bookmark_pages(self) -> Iterator[list[dict[str, Any]]]:
        """Yield pages of raw bookmark payloads, oldest cursor to newest. (TODO.)"""
        ...


def parse_bookmark(raw: dict[str, Any]) -> dict[str, Any]:
    """Turn one raw X payload into a parsed post record.

    Captures: identity, content, author, media URLs + alt-text, signals, and context
    (immediate parent for replies, quoted post for quotes, author self-thread for
    originals). Retains the raw payload verbatim. (TODO: implement.)
    """
    raise NotImplementedError("ingest-one slice")


def run_backfill(client: XClient, db_path: str) -> int:
    """Page through all bookmarks, upsert by post id (idempotent), return count. (TODO.)"""
    raise NotImplementedError("backfill slice")
