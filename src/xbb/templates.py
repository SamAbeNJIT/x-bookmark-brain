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
  :root {
    --bg: #f6f7f9; --card: #fff; --ink: #1a1d24; --muted: #6b7280;
    --line: #e6e8ec; --accent: #4263eb; --accent-soft: #eef1fe;
    --shadow: 0 1px 2px rgba(16,24,40,.06), 0 1px 3px rgba(16,24,40,.05);
  }
  * { box-sizing: border-box; }
  body { font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
         max-width: 760px; margin: 0 auto; padding: 0 1.1rem 4rem; color: var(--ink);
         line-height: 1.55; background: var(--bg); -webkit-font-smoothing: antialiased; }
  a { color: var(--accent); text-decoration: none; }
  a:hover { text-decoration: underline; }
  h1 { font-size: 1.5rem; font-weight: 700; letter-spacing: -.02em; margin: 1.4rem 0 1rem; }
  h3 { margin: 1.2rem 0 .5rem; font-size: 1rem; }
  .nav { position: sticky; top: 0; z-index: 5; display: flex; gap: .3rem; flex-wrap: wrap;
         background: rgba(246,247,249,.85); backdrop-filter: blur(8px);
         padding: .8rem 0 .7rem; margin-bottom: .4rem; border-bottom: 1px solid var(--line); }
  .nav a { padding: .35rem .7rem; border-radius: 999px; font-weight: 600; font-size: .9rem;
           color: var(--muted); }
  .nav a:hover { background: var(--accent-soft); color: var(--accent); text-decoration: none; }
  .nav a.brand { color: var(--ink); font-weight: 800; letter-spacing: -.01em; }
  .post { background: var(--card); border: 1px solid var(--line); border-radius: 12px;
          padding: .85rem 1rem; margin: .6rem 0; box-shadow: var(--shadow);
          transition: border-color .15s, transform .15s; }
  .post:hover { border-color: #cdd3df; transform: translateY(-1px); }
  .post .body { white-space: pre-wrap; }
  .post .meta { color: var(--muted); font-size: .82rem; margin-top: .45rem; }
  input[type=text], input[type=search] { width: 100%; padding: .65rem .8rem; font-size: 1rem;
         border: 1px solid #d2d6de; border-radius: 10px; background: var(--card);
         box-shadow: var(--shadow); }
  input:focus { outline: none; border-color: var(--accent);
                box-shadow: 0 0 0 3px var(--accent-soft); }
  button { padding: .55rem .9rem; font-size: .9rem; font-weight: 600; border: 0;
           background: var(--accent); color: #fff; border-radius: 9px; cursor: pointer; }
  button:hover { background: #3651c9; }
  button.ghost { background: var(--card); color: var(--muted); border: 1px solid #d2d6de; }
  form { margin: .5rem 0; }
  .answer { background: var(--card); border: 1px solid var(--line); border-left: 4px solid var(--accent);
            padding: 1rem 1.1rem; border-radius: 10px; margin: 1.1rem 0; box-shadow: var(--shadow);
            white-space: pre-wrap; }
  .row { display: flex; gap: .4rem; align-items: center; flex-wrap: wrap; }
  .muted { color: var(--muted); }
  .lead { color: var(--muted); margin-top: -.4rem; }
  .stats { display: flex; gap: .6rem; flex-wrap: wrap; margin: .2rem 0 1rem; }
  .stat { background: var(--card); border: 1px solid var(--line); border-radius: 10px;
          padding: .55rem .8rem; box-shadow: var(--shadow); font-size: .9rem; }
  .stat b { font-size: 1.15rem; display: block; }
  .badge { display: inline-block; min-width: 1.4rem; text-align: center; font-size: .75rem;
           font-weight: 600; color: var(--muted); background: #eef0f4; border-radius: 999px;
           padding: .1rem .5rem; margin-left: .4rem; }
  /* category tree */
  .tree details { background: var(--card); border: 1px solid var(--line); border-radius: 12px;
                  margin: .55rem 0; box-shadow: var(--shadow); overflow: hidden; }
  .tree summary { list-style: none; cursor: pointer; padding: .85rem 1rem; font-weight: 700;
                  font-size: 1.02rem; display: flex; align-items: center; gap: .5rem; }
  .tree summary::-webkit-details-marker { display: none; }
  .tree summary:hover { background: var(--accent-soft); }
  .tree .caret { color: var(--accent); transition: transform .18s ease; font-size: .8rem; }
  .tree details[open] .caret { transform: rotate(90deg); }
  .tree details[open] summary { border-bottom: 1px solid var(--line); }
  .tree .children { padding: .35rem .5rem .6rem; }
  .tree .child { display: flex; align-items: center; justify-content: space-between;
                 padding: .5rem .7rem; margin-left: 1.1rem; border-radius: 8px;
                 border-left: 2px solid var(--line); color: var(--ink); }
  .tree .child:hover { background: var(--accent-soft); border-left-color: var(--accent);
                       text-decoration: none; }
  .tree .grow { flex: 1; }
</style>
"""

_NAV = (
    '<div class="nav">'
    '<a class="brand" href="/">🧠 bookmark-brain</a>'
    '<a href="/ui/search">Search</a><a href="/ui/ask">Ask</a>'
    '<a href="/ui/categories">Categories</a><a href="/ui/taxonomy">Taxonomy</a>'
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
    return f'<div class="post"><div class="body">{text}</div><div class="meta">{at}{score}{link}</div></div>'
