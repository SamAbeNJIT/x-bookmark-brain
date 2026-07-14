"""Browser-bookmark import: parse a Netscape bookmark export into ingestion records.

Chrome, Firefox, Safari and Edge all export the same `NETSCAPE-Bookmark-file-1` HTML
(nested ``<DL>`` lists; ``<DT><H3>`` folders; ``<DT><A HREF ADD_DATE>`` bookmarks), so one
parser covers every browser — only the export instructions in the UI differ.

Pure and stdlib-only (``html.parser``): no network, no DB — unit-testable like the X parse
layer. ``to_record`` emits the same generic record dict the X parsers produce, so the
existing ``ingestion._upsert_post`` → embed → categorize pipeline stores these unchanged,
with ``source="browser"`` and ``author=None`` (browser bookmarks have no author; the
posts→authors FK allows NULL).

Enrichment note: ``compose_text`` is the single place the embedding/labeling text is
composed. A future page-content fetcher just passes ``page_summary`` and re-runs the
enrich job — no schema or parser changes.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlsplit

# Universal container folders every browser wraps exports in — noise, not signal.
_ROOT_FOLDERS = {
    "bookmarks", "bookmarks bar", "bookmarks menu", "bookmarks toolbar",
    "other bookmarks", "mobile bookmarks", "unsorted bookmarks", "favorites bar",
}


class _NetscapeParser(HTMLParser):
    """Walk the <DL>/<DT><H3>/<DT><A> nesting, tracking the folder stack.

    The format routinely omits closing </DT>/</p> tags; html.parser tolerates that, and the
    folder logic only relies on H3/A/DL events, which browsers do emit well-formed."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.bookmarks: list[dict[str, Any]] = []
        self._stack: list[str] = []
        self._pending_folder: str | None = None  # last <H3> text, waiting for its <DL>
        self._capture: str | None = None         # 'h3' | 'a' while inside that element
        self._buf: list[str] = []
        self._attrs: dict[str, str | None] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "h3":
            self._capture, self._buf = "h3", []
        elif tag == "a":
            self._capture, self._buf, self._attrs = "a", [], dict(attrs)
        elif tag == "dl":
            # entering a folder scope: the most recent H3 names it (the top-level DL has none)
            self._stack.append(self._pending_folder or "")
            self._pending_folder = None

    def handle_endtag(self, tag: str) -> None:
        if tag == "h3":
            self._pending_folder = "".join(self._buf).strip()
            self._capture = None
        elif tag == "a":
            url = (self._attrs.get("href") or "").strip()
            if url:
                self.bookmarks.append({
                    "url": url,
                    "title": "".join(self._buf).strip(),
                    "add_date": _parse_add_date(self._attrs.get("add_date")),
                    "folder": "/".join(
                        seg for seg in self._stack
                        if seg and seg.lower() not in _ROOT_FOLDERS
                    ),
                })
            self._capture = None
        elif tag == "dl" and self._stack:
            self._stack.pop()

    def handle_data(self, data: str) -> None:
        if self._capture:
            self._buf.append(data)


def _parse_add_date(raw: str | None) -> int | None:
    """ADD_DATE as unix seconds. Some exports use micro/milliseconds — scale those down."""
    try:
        ts = int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    while ts > 100_000_000_000:  # > ~5138 AD in seconds ⇒ ms or µs precision
        ts //= 1000
    return ts if ts > 0 else None


def parse_netscape_html(content: str) -> list[dict[str, Any]]:
    """All importable bookmarks in an export file: http(s) only (drops ``javascript:``
    bookmarklets and Firefox ``place:`` smart folders), de-duplicated by URL (first wins),
    in file order."""
    parser = _NetscapeParser()
    parser.feed(content)
    seen: set[str] = set()
    out = []
    for bm in parser.bookmarks:
        scheme = urlsplit(bm["url"]).scheme.lower()
        if scheme not in ("http", "https") or bm["url"] in seen:
            continue
        seen.add(bm["url"])
        out.append(bm)
    return out


def domain_of(url: str) -> str:
    host = urlsplit(url).netloc.lower()
    return host.removeprefix("www.")


def compose_text(title: str | None, folder: str | None, domain: str,
                 page_summary: str | None = None) -> str:
    """The embedding/labeling text for a browser bookmark: title first (what embeddings and
    the labeler key on), then folder path + domain as context signal. ``page_summary`` is
    the V2 enrichment slot — fetched page description lands here and the post re-embeds."""
    headline = title or domain
    lines = [headline]
    context = " · ".join(part for part in (folder, domain)
                         if part and part != headline)  # untitled → don't repeat the domain
    if context:
        lines.append(context)
    if page_summary:
        lines.append(page_summary)
    return "\n".join(lines)


def record_id(url: str) -> str:
    """Deterministic post id for a bookmark URL — re-importing the same export (or an updated
    one) upserts instead of duplicating, same idempotency X re-syncs get from snowflake ids."""
    return "web-" + hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]


def to_record(bm: dict[str, Any], rank: int) -> dict[str, Any]:
    """A parsed bookmark as the generic ingestion record dict (see ingestion._upsert_post)."""
    url = bm["url"]
    added = bm.get("add_date")
    iso = (datetime.fromtimestamp(added, tz=timezone.utc).isoformat() if added else None)
    return {
        "id": record_id(url),
        "source": "browser",
        "url": url,
        "text": compose_text(bm.get("title"), bm.get("folder"), domain_of(url)),
        "lang": None,
        "created_at": iso,
        "bookmarked_at": iso,
        "author": None,
        "kind": "original",
        "parent_post_id": None,
        "media": [],
        "hashtags": [],
        "links": [{"url": url}],
        "like_count": None,
        "repost_count": None,
        "raw": {"title": bm.get("title"), "folder": bm.get("folder"), "add_date": added},
        "sort_index": rank,
    }


def to_records(bookmarks: list[dict[str, Any]], base_rank: int) -> list[dict[str, Any]]:
    """Records ranked ``base_rank+1..n`` in saved order (oldest first ⇒ newest saves get the
    highest rank), matching how X incremental syncs extend the shared bm_rank space so the
    Feed interleaves sources sensibly. Undated bookmarks sort oldest."""
    ordered = sorted(bookmarks, key=lambda b: b.get("add_date") or 0)
    return [to_record(bm, base_rank + 1 + i) for i, bm in enumerate(ordered)]
