"""Tiny server-rendered HTML helpers for the local web UI.

No template engine — just escaped f-strings. Keeps the UI dependency-light and the whole
view layer in one readable place.
"""

from __future__ import annotations

import html
from typing import Any

from fastapi.responses import HTMLResponse

_STYLE = """
<style>
  body { font-family: system-ui, -apple-system, sans-serif; max-width: 820px;
         margin: 2rem auto; padding: 0 1rem; color: #222; line-height: 1.5; }
  a { color: #1d6fb8; text-decoration: none; } a:hover { text-decoration: underline; }
  h1 { font-size: 1.4rem; } h3 { margin-bottom: .4rem; }
  .nav { margin-bottom: 1.2rem; border-bottom: 1px solid #eee; padding-bottom: .6rem; }
  .nav a { margin-right: 1rem; font-weight: 600; }
  .post { border: 1px solid #e3e3e3; border-radius: 8px; padding: .7rem .9rem; margin: .6rem 0; }
  .post .meta { color: #888; font-size: .82rem; margin-top: .35rem; }
  input[type=text], input[type=search] { width: 100%; padding: .55rem; font-size: 1rem;
         box-sizing: border-box; border: 1px solid #ccc; border-radius: 6px; }
  button { padding: .45rem .8rem; font-size: .9rem; border: 1px solid #1d6fb8;
           background: #1d6fb8; color: #fff; border-radius: 6px; cursor: pointer; }
  form { margin: .5rem 0; }
  .answer { background: #f7f9fb; border-left: 3px solid #1d6fb8; padding: .8rem 1rem;
            border-radius: 4px; margin: 1rem 0; }
  .row { display: flex; gap: .4rem; align-items: center; flex-wrap: wrap; }
  .muted { color: #888; }
</style>
"""

_NAV = (
    '<div class="nav">'
    '<a href="/">home</a><a href="/ui/search">search</a><a href="/ui/ask">ask</a>'
    '<a href="/ui/categories">categories</a><a href="/ui/taxonomy">taxonomy</a>'
    "</div>"
)


def esc(value: Any) -> str:
    return html.escape(str(value)) if value is not None else ""


def page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(
        "<!doctype html><html><head><meta charset=utf-8>"
        f"<title>{esc(title)} · x-bookmark-brain</title>{_STYLE}</head>"
        f"<body>{_NAV}<h1>{esc(title)}</h1>{body}</body></html>"
    )


def post_card(p: dict[str, Any]) -> str:
    text = esc(p.get("text") or "")
    handle = esc(p.get("handle") or "")
    url = p.get("url")
    link = f' · <a href="{esc(url)}" target="_blank" rel="noopener">open ↗</a>' if url else ""
    score = f' · score {p["score"]:.2f}' if isinstance(p.get("score"), (int, float)) else ""
    at = f"@{handle}" if handle else ""
    return f'<div class="post"><div>{text}</div><div class="meta">{at}{score}{link}</div></div>'
