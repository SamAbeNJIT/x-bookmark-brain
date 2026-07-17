"""Tiny server-rendered HTML helpers for the local web UI.

No template engine — just escaped f-strings. Keeps the UI dependency-light and the whole
view layer in one readable place.
"""

from __future__ import annotations

import html
import json
import re
from typing import Any
from urllib.parse import quote, urlsplit

from fastapi.responses import HTMLResponse

# One off-color per top-level group — distinct but muted, used for card tints + the legend.
PARENT_COLORS: dict[str, str] = {
    "AI & Engineering": "#5b6cf0",   # indigo
    "Culture & Media": "#a45cd6",    # violet
    "Politics & Society": "#e05569", # rose
    "Finance & Crypto": "#d99a1c",   # amber
    "Health & Longevity": "#2faa6f", # green
    "Science & Industry": "#2aa7bd", # teal
    "Other": "#9aa0ab",              # gray
}

_SOURCE_META = {
    "x": {"label": "𝕏 X", "author_base": "https://x.com/"},
    "browser": {"label": "🌐 Web", "author_base": None},
    "reddit": {"label": "👽 Reddit", "author_base": "https://www.reddit.com/user/"},
    "github": {"label": "🐙 GitHub", "author_base": "https://github.com/"},
}
_SOURCE_LABELS = {source: meta["label"] for source, meta in _SOURCE_META.items()}


def parent_color(parent: str | None) -> str | None:
    return PARENT_COLORS.get(parent) if parent else None


def legend(
    groups: list[tuple[str, int]], active: str | None = None, *, graph_mode: bool = False
) -> str:
    """Color legend; links filter feeds unless graph mode turns them into focus controls."""
    legend_class = "legend graph-legend" if graph_mode else "legend"
    mode_attr = ' data-mode="graph"' if graph_mode else ""
    all_cls = " active" if active is None else ""
    href = "/ui/graph" if graph_mode else "/ui/feed"

    def graph_attrs(parent: str, pressed: bool = False) -> str:
        return (f' data-parent="{esc(parent)}" aria-pressed="{str(pressed).lower()}"'
                if graph_mode else "")

    chips = [f'<a class="chip{all_cls}" href="{href}"{graph_attrs("", True)}>All</a>']
    for parent, count in groups:
        c = PARENT_COLORS.get(parent, PARENT_COLORS["Other"])
        cls = " active" if parent == active else ""
        chip_href = href if graph_mode else f"/ui/feed?parent={quote(parent)}"
        chips.append(
            f'<a class="chip{cls}" href="{chip_href}" style="--c:{c}"{graph_attrs(parent)}>'
            f'<span class="sw"></span>{esc(parent)}'
            f'<span class="badge">{count:,}</span></a>'
        )
    return f'<div class="{legend_class}"{mode_attr}>{"".join(chips)}</div>'

_HEAD = (
    '<link rel="icon" type="image/png" sizes="32x32" href="/static/favicon-32.png">'
    '<link rel="apple-touch-icon" href="/static/apple-touch-icon.png">'
    '<link rel="preconnect" href="https://fonts.googleapis.com">'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
    '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?'
    'family=Space+Grotesk:wght@500;600;700&display=swap">'
)

_STYLE = """
<style>
  :root {
    --bg: #f4f3ef; --panel: #ffffff; --ink: #191a1e; --muted: #74767e;
    --line: #e9e6df; --line-2: #e0ddd4;
    --accent: #5b53e8; --accent-ink: #4a43d4; --accent-soft: #edecfd;
    --sidebar: #16161b; --side-ink: #9a9ca6; --side-hover: #23242c;
    --shadow: 0 1px 2px rgba(20,18,30,.05), 0 6px 22px rgba(20,18,30,.05);
    --radius: 16px; --display: "Space Grotesk", ui-sans-serif, system-ui, sans-serif;
  }
  * { box-sizing: border-box; }
  html { scroll-behavior: smooth; }
  body { font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
         margin: 0; color: var(--ink); line-height: 1.55; background: var(--bg);
         -webkit-font-smoothing: antialiased; display: flex; min-height: 100vh; }
  a { color: var(--accent-ink); text-decoration: none; }
  a:hover { text-decoration: underline; }

  /* sidebar */
  .sidebar { position: fixed; inset: 0 auto 0 0; width: 224px; background: var(--sidebar);
             color: var(--side-ink); display: flex; flex-direction: column; padding: 1.4rem 1rem;
             gap: .15rem; }
  .brand { font-family: var(--display); font-weight: 700; font-size: 1.22rem; color: #fff;
           letter-spacing: -.02em; line-height: 1.15; padding: .2rem .6rem 1.3rem; }
  .brand .dot { color: var(--accent); }
  .sidebar nav { display: flex; flex-direction: column; gap: .12rem; }
  .sidebar nav a { color: var(--side-ink); font-weight: 500; font-size: .94rem;
                   padding: .56rem .7rem; border-radius: 10px; display: flex; gap: .6rem;
                   align-items: center; transition: background .14s, color .14s; }
  .sidebar nav a:hover { background: var(--side-hover); color: #fff; text-decoration: none; }
  .sidebar nav a.active { background: var(--accent); color: #fff; }
  .sidebar nav a .ic { width: 1.1rem; text-align: center; opacity: .9; }
  .side-foot { margin-top: auto; font-size: .76rem; color: #5b5d68; padding: .6rem; }
  /* rail mode: results pages collapse the sidebar to an icon rail so cards get the width */
  .brand-mini { display: none; }
  @media (min-width: 761px) {
    body.rail .sidebar { width: 64px; align-items: center; padding: 1rem .5rem; }
    body.rail .brand, body.rail .side-foot, body.rail .sidebar nav a .lbl { display: none; }
    body.rail .brand-mini { display: block; font-family: var(--display); font-weight: 700;
                            font-size: 1.25rem; color: #fff; margin-bottom: 1.1rem; }
    body.rail .sidebar nav a { justify-content: center; padding: .6rem; }
    body.rail .sidebar nav a .ic { width: auto; font-size: 1.05rem; }
    body.rail .content { margin-left: 64px; }
  }

  /* content */
  .content { margin-left: 224px; flex: 1; padding: 2.4rem clamp(1.2rem, 4vw, 3.5rem) 5rem; }
  .wrap { max-width: 1320px; margin: 0 auto; }
  .wrap.wide { max-width: 1920px; }  /* results pages stretch to the screen */
  /* reading-width blocks stay comfortable even on huge screens */
  .narrow { max-width: 720px; }
  /* card lists: JS distributes cards into the shortest column in order, so they read
     newest-first left-to-right across the top row AND pack with no gaps (masonry). */
  .cards { display: flex; align-items: flex-start; gap: .85rem; flex-wrap: wrap; }
  .masonry-col { flex: 1 1 0; min-width: 0; display: flex; flex-direction: column; gap: .85rem; }
  .cards > .post { flex: 1 1 320px; margin: 0; }  /* brief pre-JS fallback before columns form */
  /* list view (feed toggle): one wide card per row, timeline-style; masonry JS skips these.
     Width tracks the container (readable floor, generous ceiling) instead of a fixed px. */
  .cards.list { display: block; max-width: clamp(36rem, 82%, 64rem); }
  .cards.list > .post { width: 100%; margin: 0 0 .85rem; }
  .view-toggle { float: right; font-size: .82rem; }
  .view-toggle a { color: var(--muted); text-decoration: none; padding: .25rem .6rem;
                   border: 1px solid var(--line-2); border-radius: 8px; margin-left: .35rem; }
  .view-toggle a.on { color: var(--accent-ink); background: var(--accent-soft);
                      border-color: var(--accent-soft); font-weight: 600; }
  h1 { font-family: var(--display); font-size: 1.7rem; font-weight: 700; letter-spacing: -.025em;
       margin: 0 0 1.1rem; }
  h3 { font-family: var(--display); margin: 1.4rem 0 .5rem; font-size: 1.02rem; font-weight: 600; }
  .lead { color: var(--muted); margin: -.5rem 0 1.3rem; font-size: 1rem; }
  .muted { color: var(--muted); }

  /* cards */
  .post { background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius);
          padding: 1rem 1.1rem; margin: .7rem 0; box-shadow: var(--shadow);
          transition: border-color .15s, transform .15s, box-shadow .15s; }
  .post:hover { border-color: var(--line-2); transform: translateY(-2px);
                box-shadow: 0 2px 4px rgba(20,18,30,.06), 0 12px 30px rgba(20,18,30,.08); }
  .post .head { display: flex; align-items: center; gap: .6rem; margin-bottom: .6rem; }
  .avatar { width: 40px; height: 40px; border-radius: 50%; object-fit: cover;
            background: #e7e4dc; flex: 0 0 auto; }
  .handle { font-weight: 600; color: var(--ink); font-size: .95rem; }
  .handle:hover { color: var(--accent-ink); }
  .post .body { white-space: pre-wrap; font-size: .98rem; }
  .media-row { display: flex; gap: .5rem; flex-wrap: wrap; margin-top: .75rem; }
  .media { max-width: 240px; max-height: 240px; border-radius: 12px;
           border: 1px solid var(--line); object-fit: cover; display: block; transition: transform .15s; }
  .media:hover { transform: scale(1.02); }
  .post .meta { color: var(--muted); font-size: .8rem; margin-top: .65rem; }

  /* forms */
  input[type=text], input[type=search], textarea { width: 100%; padding: .8rem 1rem;
         font-size: 1.02rem; border: 1px solid var(--line-2); border-radius: 12px;
         background: var(--panel); box-shadow: var(--shadow); font-family: inherit; }
  textarea { resize: vertical; min-height: 4.5rem; line-height: 1.5; display: block; }
  input:focus, textarea:focus { outline: none; border-color: var(--accent);
                box-shadow: 0 0 0 4px var(--accent-soft); }
  button { padding: .6rem 1.05rem; font-size: .92rem; font-weight: 600; border: 0;
           background: var(--accent); color: #fff; border-radius: 11px; cursor: pointer;
           transition: background .14s, transform .1s; font-family: inherit; }
  button:hover { background: var(--accent-ink); }
  button:active { transform: translateY(1px); }
  button.ghost { background: var(--panel); color: var(--muted); border: 1px solid var(--line-2); }
  form { margin: .5rem 0; }
  .row { display: flex; gap: .5rem; align-items: center; flex-wrap: wrap; }
  .thinking { display: inline-flex; align-items: center; gap: .5rem; color: var(--muted);
              font-size: .9rem; }
  .thinking[hidden] { display: none; }  /* author display rule otherwise defeats [hidden] */
  .spinner { width: 1.05rem; height: 1.05rem; border: 2px solid var(--line-2);
             border-top-color: var(--accent); border-radius: 50%; display: inline-block;
             animation: spin .7s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }

  /* answer */
  .answer { background: linear-gradient(180deg, #fbfbff, var(--panel)); border: 1px solid var(--line);
            border-left: 4px solid var(--accent); padding: 1.1rem 1.2rem; border-radius: 12px;
            margin: 1.2rem 0; max-width: 760px; box-shadow: var(--shadow); white-space: pre-wrap;
            font-size: .98rem; }
  .answer p { margin: 0 0 .7rem; white-space: normal; }
  .answer p:last-child { margin-bottom: 0; }

  /* ask results: answer keeps a comfortable reading width; tweets absorb ALL remaining space.
     The left pane is a chat column: the thread scrolls in .ask-scroll, the composer docks at
     the bottom (Claude/ChatGPT style). */
  .ask-cols { display: grid; grid-template-columns: minmax(340px, 46ch) 1fr;
              gap: 1.4rem; align-items: start; }
  .ask-left { position: sticky; top: 1rem; height: calc(100vh - 2rem);
              display: flex; flex-direction: column; min-height: 0; }
  .ask-scroll { flex: 1; overflow-y: auto; min-height: 0; position: relative;
                padding-right: .35rem; }
  .ask-composer { margin-top: .65rem; border-top: 1px solid var(--line); padding-top: .65rem; }
  .ask-composer form { margin: 0; }
  .ask-left .answer { margin: 0 0 .9rem; max-width: none; white-space: normal; }
  .ask-right h3 { margin-top: 0; }
  .ask-right h3.src-group { margin: 1.5rem 0 .5rem; font-size: .92rem; color: var(--muted);
                            font-weight: 600; }
  @media (max-width: 900px) { .ask-cols { grid-template-columns: 1fr; }
    .ask-left { position: static; height: auto; display: block; }
    .ask-scroll { overflow: visible; padding-right: 0; } }

  /* long tweets collapse; tap to expand */
  .post .body.clamp { display: -webkit-box; -webkit-line-clamp: 8; -webkit-box-orient: vertical;
                      overflow: hidden; }
  .post a.more { display: inline-block; margin-top: .35rem; font-size: .82rem;
                 color: var(--accent); text-decoration: none; font-weight: 600; }

  /* stats */
  .stats { display: flex; gap: .7rem; flex-wrap: wrap; margin: 0 0 1.4rem; }
  .stat { background: var(--panel); border: 1px solid var(--line); border-radius: 14px;
          padding: .85rem 1.1rem; box-shadow: var(--shadow); min-width: 7rem; }
  .stat b { font-family: var(--display); font-size: 1.5rem; display: block; letter-spacing: -.02em; }
  .stat span { font-size: .82rem; color: var(--muted); }
  a.stat { color: var(--ink); transition: border-color .14s, transform .14s; }
  a.stat:hover { border-color: var(--accent); transform: translateY(-1px); text-decoration: none; }
  .badge { display: inline-block; min-width: 1.4rem; text-align: center; font-size: .76rem;
           font-weight: 600; color: var(--muted); background: #efece4; border-radius: 999px;
           padding: .14rem .55rem; }

  /* color legend */
  .legend { display: flex; gap: .5rem; flex-wrap: wrap; margin: 0 0 1.3rem; }
  .chip { display: inline-flex; align-items: center; gap: .5rem; padding: .45rem .85rem;
          border-radius: 999px; border: 1px solid var(--line-2); background: var(--panel);
          color: var(--ink); font-size: .87rem; font-weight: 500; box-shadow: var(--shadow);
          transition: border-color .14s, background .14s; }
  .chip:hover { text-decoration: none; border-color: var(--c, var(--accent)); }
  .chip .sw { width: .8rem; height: .8rem; border-radius: 50%; flex: 0 0 auto;
              background: var(--c, var(--muted)); }
  .chip .badge { background: transparent; color: var(--muted); padding: 0; margin-left: .05rem; }
  .chip.active { border-color: var(--c, var(--accent));
                 background: color-mix(in srgb, var(--c, #5b6cf0) 14%, var(--panel)); font-weight: 600; }
  .graph-legend .chip.unavailable { opacity: .38; box-shadow: none; pointer-events: none; }

  /* category tree */
  .tree { max-width: 880px; }
  .tree .sw { width: .85rem; height: .85rem; border-radius: 50%; flex: 0 0 auto;
              background: var(--c, var(--muted)); }
  .tree details { background: var(--panel); border: 1px solid var(--line); border-radius: 14px;
                  margin: .6rem 0; box-shadow: var(--shadow); overflow: hidden; }
  .tree summary { list-style: none; cursor: pointer; padding: 1rem 1.1rem; font-weight: 600;
                  font-family: var(--display); font-size: 1.06rem; display: flex; align-items: center;
                  gap: .6rem; transition: background .14s; }
  .tree summary::-webkit-details-marker { display: none; }
  .tree summary:hover { background: var(--accent-soft); }
  .tree .caret { color: var(--accent); transition: transform .2s ease; font-size: .7rem; }
  .tree details[open] .caret { transform: rotate(90deg); }
  .tree details[open] summary { border-bottom: 1px solid var(--line); }
  .tree .children { padding: .45rem .6rem .7rem; }
  .tree .child { display: flex; align-items: center; gap: .5rem; padding: .58rem .8rem;
                 margin: .12rem 0 .12rem 1.1rem; border-radius: 10px; border-left: 2px solid var(--line-2);
                 color: var(--ink); transition: background .12s, border-color .12s; }
  .tree .child:hover { background: var(--accent-soft); border-left-color: var(--accent);
                       text-decoration: none; }
  .tree .grow { flex: 1; }

  /* user-centered knowledge graph */
  .content:has(.graph-shell) { height: 100vh; overflow: hidden;
                                padding: 18px clamp(16px,2.2vw,30px); }
  .wrap.wide:has(.graph-shell) { height: 100%; display: flex; flex-direction: column;
                                 min-height: 0; }
  .wrap:has(.graph-shell) > h1 { order: 1; margin: 0; font-size: 1.62rem; }
  .wrap:has(.graph-shell) > .lead { order: 2; margin: .05rem 0 .7rem; font-size: .9rem; }
  .graph-toolbar { display: flex; gap: .55rem; align-items: center; flex-wrap: wrap;
                   margin: 0 0 .65rem; order: 3; min-height: 36px; }
  .graph-toolbar button, .graph-toolbar input, .graph-toolbar label { font-size: .8rem; }
  .graph-toolbar button { background: var(--panel); color: var(--muted);
                          border: 1px solid var(--line-2); padding: .48rem .72rem; }
  .graph-toolbar button.on { background: var(--accent-soft); color: var(--accent-ink);
                             border-color: #cbc7fa; }
  .graph-toolbar .graph-search { width: min(240px, 100%); margin-left: auto;
                                 padding: .48rem .72rem; box-shadow: none; }
  .wrap:has(.graph-shell) > .legend { order: 4; margin: 0 0 .7rem; flex-wrap: nowrap;
                                     overflow-x: auto; scrollbar-width: none; }
  .wrap:has(.graph-shell) > .legend::-webkit-scrollbar { display: none; }
  .wrap:has(.graph-shell) > .legend .chip { padding: .34rem .58rem; font-size: .75rem;
                                            white-space: nowrap; }
  .graph-shell { display: grid; grid-template-columns: minmax(0, 1fr) 320px; gap: 1rem;
                 order: 5; flex: 1; min-height: 0; }
  .graph-wrap { --graph-ai:#5b6cf0; --graph-culture:#a45cd6; --graph-politics:#e05569;
                --graph-finance:#d99a1c; --graph-health:#2faa6f; --graph-science:#2aa7bd;
                --graph-other:#9aa0ab; position: relative; min-height: 0; height: 100%;
                background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius);
                overflow: hidden; box-shadow: var(--shadow); }
  .graph-wrap svg { width: 100%; height: 100%; display: block; }
  .graph-fallback { position: absolute; inset: 0; z-index: 4; display: grid; place-items: center;
                    padding: 2rem; text-align: center; background: var(--panel); color: var(--muted); }
  .graph-fallback[hidden] { display: none; }
  .graph-stats { position: absolute; left: .8rem; top: .8rem; z-index: 2; display: flex;
                 gap: .35rem; flex-wrap: wrap; pointer-events: none; }
  .graph-stats span { border: 1px solid var(--line); background: rgba(255,255,255,.92);
                      border-radius: 999px; padding: .25rem .55rem; font-size: .72rem; }
  .graph-stats .graph-focus-stat { border-color: var(--c, var(--accent)); font-weight: 700; }
  .graph-zoom { position: absolute; left: .7rem; bottom: .7rem; z-index: 2; display: flex;
                flex-direction: column; gap: 0; border-radius: 10px; overflow: hidden; }
  .graph-zoom button { width: 2rem; height: 2rem; padding: 0; background: var(--panel);
                       color: var(--ink); border: 1px solid var(--line-2); }
  .graph-zoom button + button { border-top: 0; }
  .graph-preview { min-height: 0; background: var(--panel); border: 1px solid var(--line);
                   border-radius: var(--radius); padding: 1rem; box-shadow: var(--shadow);
                   overflow: auto; }
  .graph-preview-empty { height: 100%; display: flex; flex-direction: column; align-items: center;
                         justify-content: center; text-align: center; gap: .45rem; }
  .graph-user-mini { width: 58px; height: 58px; border-radius: 50%; display: grid;
                     place-items: center; background: var(--accent-soft); color: var(--accent-ink);
                     font-weight: 700; border: 1px solid #cbc7fa; }
  .graph-preview-title { font-size: 1.05rem; font-weight: 700; color: var(--ink); }
  .graph-preview h3 { margin-top: .25rem; }
  .graph-preview .graph-path { color: var(--muted); font-size: .8rem; }
  .graph-preview a { display: inline-block; margin-top: .8rem; font-weight: 600; }
  .graph-panel-head { display: flex; align-items: center; justify-content: space-between;
                      gap: .5rem; padding-bottom: .75rem; border-bottom: 1px solid var(--line); }
  .graph-panel-theme { display: inline-flex; align-items: center; gap: .45rem; font-weight: 700; }
  .graph-panel-theme i, .graph-neighbor i { width: .65rem; height: .65rem; border-radius: 50%;
                                           background: var(--c); flex: 0 0 auto; }
  .graph-path-card { border: 1px solid var(--line); background: #faf9f6; border-radius: 11px;
                     padding: .65rem .7rem; margin: .8rem 0; font-size: .78rem; font-weight: 600; }
  .graph-metrics { display: grid; grid-template-columns: 1fr 1fr; gap: .45rem; margin: .8rem 0; }
  .graph-metric { border: 1px solid var(--line); border-radius: 9px; padding: .45rem .55rem; }
  .graph-metric small { display: block; color: var(--muted); text-transform: uppercase;
                        font-size: .62rem; }
  .graph-metric b { font-size: 1rem; }
  .graph-neighbors h4 { margin: .8rem 0 .35rem; color: var(--muted); font-size: .68rem;
                        text-transform: uppercase; letter-spacing: .05em; }
  .graph-neighbor { display: flex; align-items: center; gap: .45rem; padding: .42rem .3rem;
                    border-radius: 8px; font-size: .76rem; }
  .graph-neighbor:first-of-type { background: var(--accent-soft); }
  .graph-neighbor span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  @media (max-width: 1120px) { .graph-shell { grid-template-columns: 1fr; }
    .graph-preview { display: none; }
    .graph-toolbar .graph-search { margin-left: 0; } }

  @media (max-width: 760px) {
    body { flex-direction: column; }
    .sidebar { position: static; width: auto; flex-direction: row; align-items: center;
               padding: .6rem .8rem; gap: .2rem; overflow-x: auto; scrollbar-width: none; }
    .sidebar::-webkit-scrollbar { display: none; }
    .brand { padding: .2rem .5rem; font-size: 1.05rem; }
    .sidebar nav { flex-direction: row; }
    .sidebar nav a .ic { display: none; }
    .side-foot { display: none; }
    .content { margin-left: 0; padding: 1.4rem 1.1rem 3rem; }
    .content:has(.graph-shell) { height: auto; min-height: calc(100vh - 79px);
                                 overflow: visible; padding: 1rem; }
    .wrap.wide:has(.graph-shell) { height: auto; }
    .wrap:has(.graph-shell) > .legend { flex-wrap: wrap; overflow: visible; }
    .graph-shell { flex: none; height: 340px; }
    .graph-wrap { display: flex; flex-direction: column; }
    .graph-wrap svg { height: auto; flex: 1; min-height: 0; }
    .graph-stats { position: static; order: -1; padding: .7rem .7rem 0;
                   pointer-events: none; }
  }
</style>
"""

_NAV_ITEMS = [
    ("/", "Home", "◆"),
    ("/ui/search", "Search", "⌕"),
    ("/ui/ask", "Ask", "✦"),
    ("/ui/categories", "Categories", "▤"),
    ("/ui/feed", "Feed", "▦"),
    ("/ui/graph", "Graph", "◎"),
    ("/ui/taxonomy", "Taxonomy", "⚙"),
    ("/ui/refresh", "Sync", "↻"),
    ("/ui/import", "Import", "⤒"),
    ("/ui/billing", "Billing", "◈"),
    ("/ui/feedback", "Feedback", "✉"),
]

_SIDEBAR = (
    '<aside class="sidebar">'
    '<div class="brand">bookmark<span class="dot">.</span><br>brain</div>'
    '<div class="brand-mini">b<span class="dot" style="color:var(--accent)">.</span></div>'
    "<nav>"
    + "".join(
        f'<a href="{href}" title="{label}"><span class="ic">{ic}</span>'
        f'<span class="lbl">{label}</span></a>'
        for href, label, ic in _NAV_ITEMS
    )
    + "</nav>"
    '<div class="side-foot">private · AI-searched<br>'
    '<a href="/terms" style="color:inherit">terms</a> · '
    '<a href="/privacy" style="color:inherit">privacy</a><br>'
    "not affiliated with X Corp.</div>"
    "</aside>"
)

_ACTIVE_JS = (
    "<script>document.querySelectorAll('.sidebar nav a').forEach(function(a){"
    "var h=a.getAttribute('href');var p=location.pathname;"
    # exact match, or a true sub-path (h + '/'): plain prefix matching would light up
    # /ui/feed while on /ui/feedback.
    "if(h===p||(h!=='/'&&p.indexOf(h+'/')===0)){a.classList.add('active');"
    "if(innerWidth<=760)a.scrollIntoView({block:'nearest',inline:'center'});}});</script>"
)

# Row-major masonry: distribute cards (in DOM/chronological order) into the shortest column,
# so the top row is the newest items left-to-right and columns pack with no gaps. Exposes
# window.__masonryAdd(container, html) for the feed's infinite scroll to append more.
# Collapse long tweet bodies to 8 lines with a Show more/less toggle. Defined here but run
# AFTER masonry lays the columns out (order in page()): the clamp decision measures
# scrollHeight at the card's real column width — measuring on the wide pre-masonry fallback
# under-detected long tweets, leaving them expanded with no way to collapse.
_CLAMP_JS = (
    "<script>window.__clampCards=function(root){"
    "(root||document).querySelectorAll('.post .body:not([data-clamped])').forEach(function(b){"
    "b.setAttribute('data-clamped','1');b.classList.add('clamp');"
    "if(b.scrollHeight<=b.clientHeight+2){b.classList.remove('clamp');return;}"
    "var t=document.createElement('a');t.href='javascript:void(0)';t.className='more';"
    "t.textContent='Show more';"
    "t.onclick=function(){var c=b.classList.toggle('clamp');"
    "t.textContent=c?'Show more':'Show less';};"
    "b.after(t);});};</script>"
)

_MASONRY_JS = (
    "<script>(function(){"
    "function nCols(c){var w=c.clientWidth||c.offsetWidth||0;return Math.max(1,Math.floor((w+14)/354));}"
    "function shortest(c){var k=c.querySelectorAll('.masonry-col'),m=k[0];"
    "for(var i=1;i<k.length;i++){if(k[i].offsetHeight<m.offsetHeight)m=k[i];}return m;}"
    "function ordered(c){"  # collect cards in their original chronological order, not column-grouped
    "var a=Array.prototype.slice.call(c.querySelectorAll('.post')),mx=0;"
    "a.forEach(function(x){var o=x.getAttribute('data-ord');if(o!==null)mx=Math.max(mx,+o+1);});"
    "a.forEach(function(x){if(x.getAttribute('data-ord')===null)x.setAttribute('data-ord',mx++);});"
    "a.sort(function(p,q){return (+p.getAttribute('data-ord'))-(+q.getAttribute('data-ord'));});return a;}"
    "function build(c){var cards=ordered(c),want=nCols(c);"
    "c.innerHTML='';for(var i=0;i<want;i++){var d=document.createElement('div');d.className='masonry-col';c.appendChild(d);}"
    "cards.forEach(function(card){shortest(c).appendChild(card);});}"
    "function layout(){document.querySelectorAll('.cards:not(.list)').forEach(build);}"
    "window.__masonryAdd=function(c,html){"
    "if(c.classList.contains('list')){c.insertAdjacentHTML('beforeend',html);"
    "if(window.__clampCards)__clampCards(c);return;}"
    "if(!c.querySelector('.masonry-col'))build(c);"
    "var mx=0;c.querySelectorAll('.post').forEach(function(x){var o=x.getAttribute('data-ord');if(o!==null)mx=Math.max(mx,+o+1);});"
    "var t=document.createElement('div');t.innerHTML=html;"
    "Array.prototype.slice.call(t.querySelectorAll('.post')).forEach(function(card){"
    "card.setAttribute('data-ord',mx++);shortest(c).appendChild(card);});"
    "if(window.__clampCards)__clampCards(c);};"
    "window.__masonryLayout=layout;"
    # layout -> clamp at final column width -> relayout so columns rebalance on clamped heights
    "layout();if(window.__clampCards){__clampCards();layout();}"
    "window.addEventListener('load',layout);"
    "var tid;window.addEventListener('resize',function(){clearTimeout(tid);tid=setTimeout(layout,150);});"
    "})();</script>"
)


def esc(value: Any) -> str:
    return html.escape(str(value)) if value is not None else ""


def md_lite(text: str | None) -> str:
    """Render the small markdown subset LLM answers use (**bold**, *italic*, bullets,
    paragraphs) to HTML. Escapes FIRST, so model output can never inject markup."""
    s = esc(text or "")
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s, flags=re.DOTALL)
    s = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<i>\1</i>", s)
    out = []
    for para in s.split("\n\n"):
        if not para.strip():
            continue
        lines = [("• " + ln.lstrip()[2:] if ln.lstrip().startswith(("- ", "* ")) else ln)
                 for ln in para.split("\n")]
        out.append("<p>" + "<br>".join(lines) + "</p>")
    return "".join(out)


def page(title: str, body: str, wide: bool = False, rail: bool = False) -> HTMLResponse:
    """`wide` stretches the content wrap; `rail` also collapses the sidebar to an icon rail
    (results pages where every horizontal pixel goes to cards)."""
    wrap = "wrap wide" if wide else "wrap"
    body_cls = ' class="rail"' if rail else ""
    return HTMLResponse(
        "<!doctype html><html lang=en><head><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width, initial-scale=1'>"
        f"<title>{esc(title)} · bookmark-brain</title>{_HEAD}{_STYLE}</head>"
        f"<body{body_cls}>{_SIDEBAR}<main class=\"content\"><div class=\"{wrap}\">"
        f"<h1>{esc(title)}</h1>{body}</div></main>{_ACTIVE_JS}{_CLAMP_JS}{_MASONRY_JS}</body></html>"
    )


def graph_visualization(fallback: str) -> str:
    """Interactive, user-centered graph markup; the server fallback stays until D3 renders."""
    return (
        '<div class="graph-toolbar" data-component-id="graph-toolbar">'
        '<button class="on" id="graph-centered">Centered</button>'
        '<button id="graph-free">Free force</button>'
        '<label for="graph-threshold">Similarity ≥ <b id="graph-threshold-value">0.50</b></label>'
        '<input id="graph-threshold" type="range" min="0" max="100" value="50">'
        '<button class="on" id="graph-edges" aria-pressed="true">Edges: on</button>'
        '<button id="graph-reset">Reset</button><button id="graph-center">◎ Center on me</button>'
        '<input class="graph-search" id="graph-search" type="search" '
        'placeholder="Find a bookmark…" aria-label="Find a bookmark"></div>'
        '<div class="graph-shell"><div id="graph" class="graph-wrap" data-src="/ui/graph/data" '
        'data-node-types="user theme post" '
        'data-edge-kinds="ownership theme similarity membership" '
        'data-layout="user-centered" data-selection-path="post theme user">'
        f'<div id="graph-fallback" class="graph-fallback">{fallback}</div>'
        '<div class="graph-stats" id="graph-stats" aria-live="polite"></div>'
        '<div class="graph-zoom" data-component-id="zoom-controls">'
        '<button id="graph-zoom-in" aria-label="Zoom in">+</button>'
        '<button id="graph-zoom-out" aria-label="Zoom out">−</button>'
        '<button id="graph-fit" aria-label="Fit to screen">⛶</button></div></div>'
        '<aside class="graph-preview" id="graph-preview">'
        '<div class="graph-preview-empty"><div class="graph-user-mini">You</div>'
        '<div class="graph-preview-title">Your knowledge graph</div>'
        '<p class="muted">Every theme belongs to one connected personal library. Select a '
        'bookmark to trace it through its theme hub back to you, then inspect its closest '
        'semantic neighbors across the graph.</p></div></aside></div>'
        '<script src="https://cdn.jsdelivr.net/npm/d3@7.9.0/dist/d3.min.js"></script>'
        + _GRAPH_JS
    )


_GRAPH_JS = r"""
<script>(function(){
var host=document.getElementById('graph'), fallback=document.getElementById('graph-fallback');
if(!host||!window.d3){return;} // CDN failure deliberately leaves the server fallback visible.
fetch('/ui/graph/data').then(function(r){if(!r.ok)throw new Error('graph data');return r.json();})
.then(function(data){
  var visible=data.nodes.filter(function(n){return n.type==='user'||n.type==='theme'||n.type==='post';});
  var ids=new Set(visible.map(function(n){return n.id;}));
  var rawLinks=data.edges.filter(function(e){return ids.has(e.source)&&ids.has(e.target)&&
    (e.kind==='ownership'||e.kind==='theme'||e.kind==='similarity'||e.kind==='membership');});
  var byId=new Map(visible.map(function(n){return[n.id,n];}));
  var themeFor=new Map(), themeEdgeForPost=new Map(), degree=new Map(), selected=null,
    focusParent=null, searchQuery='', edgesOn=true, threshold=.5;
  rawLinks.forEach(function(e){
    if(e.kind==='theme'){themeFor.set(e.source,e.target);themeEdgeForPost.set(e.source,e);}
    if(e.kind==='similarity'){degree.set(e.source,(degree.get(e.source)||0)+1);
      degree.set(e.target,(degree.get(e.target)||0)+1);}
  });
  var cross=rawLinks.filter(function(e){return e.kind==='similarity'&&
    byId.get(e.source).parent!==byId.get(e.target).parent;}).sort(function(a,b){return (b.weight||0)-(a.weight||0);});
  var strongestBridges=new Set(cross.slice(0,16));
  var links=rawLinks.filter(function(e){return e.kind!=='similarity'||
    byId.get(e.source).parent===byId.get(e.target).parent||strongestBridges.has(e);});
  var bridges=strongestBridges.size;
  var stats=document.getElementById('graph-stats');
  function renderStats(){stats.innerHTML='<span><b>'+data.meta.post_nodes+
    '</b> bookmarks</span><span><b>'+data.meta.similarity_edges+'</b> links</span><span><b>'+bridges+'</b> bridges</span>';
    if(focusParent){var focus=document.createElement('span'),theme=themes.find(function(n){return n.label===focusParent;});
      focus.className='graph-focus-stat';focus.style.setProperty('--c',(theme&&theme.color)||'#5b53e8');focus.textContent='Focused: '+focusParent;stats.appendChild(focus);}}
  var svg=d3.select(host).insert('svg',':first-child');
  var w=svg.node().clientWidth,h=svg.node().clientHeight;
  var scene=svg.append('g'), haloLayer=scene.append('g'), linkLayer=scene.append('g'), nodeLayer=scene.append('g');
  var zoom=d3.zoom().scaleExtent([.25,5]).on('zoom',function(e){scene.attr('transform',e.transform);});
  svg.call(zoom);
  var themes=visible.filter(function(n){return n.type==='theme';});
  var root=byId.get('user:me'), centered=true;
  function radial(){
    w=svg.node().clientWidth;h=svg.node().clientHeight;root.fx=w/2;root.fy=h/2;
    var radius=w<500 ? Math.min(w,h)*.30 : Math.max(145,Math.min(w,h)*.31);
    themes.forEach(function(n,i){var a=(Math.PI*2*i/themes.length)-Math.PI/2;
      n.fx=w/2+Math.cos(a)*radius;n.fy=h/2+Math.sin(a)*radius;});
  }
  radial();
  visible.filter(function(n){return n.type==='post';}).forEach(function(n,i){
    var theme=byId.get(themeFor.get(n.id)),a=(i*2.399963229728653),r=28+(i%4)*12;
    n.x=(theme?theme.fx:w/2)+Math.cos(a)*r;n.y=(theme?theme.fy:h/2)+Math.sin(a)*r;
  });
  var force=d3.forceSimulation(visible)
    .force('link',d3.forceLink(links).id(function(d){return d.id;}).distance(function(e){
      return e.kind==='ownership'?150:e.kind==='theme'?62:85;}).strength(function(e){
      return e.kind==='ownership' ? .65 : e.kind==='theme' ? .2 : .11;}))
    .force('charge',d3.forceManyBody().strength(function(n){return n.type==='post'?-34:-100;}))
    .force('collide',d3.forceCollide().radius(function(n){return n.type==='user'?43:n.type==='theme'?30:8;}))
    .force('x',d3.forceX(w/2).strength(.025)).force('y',d3.forceY(h/2).strength(.025));
  var halo=haloLayer.selectAll('circle').data(themes).join('circle').attr('fill',function(d){return d.color;})
    .attr('fill-opacity',.065).attr('stroke',function(d){return d.color;}).attr('stroke-opacity',.18)
    .attr('stroke-dasharray','3 6').attr('r',function(d){return 54+Math.sqrt(d.count||1)*7;})
    .attr('data-parent',function(d){return d.label;}).style('cursor','pointer')
    .on('click',function(e,d){e.stopPropagation();setThemeFocus(d.label);});
  var path=linkLayer.selectAll('path').data(links).join('path').attr('fill','none')
    .attr('data-edge-kind',function(e){return e.kind;})
    .attr('data-source',function(e){return edgeId(e.source);}).attr('data-target',function(e){return edgeId(e.target);})
    .attr('stroke',function(e){if(e.kind==='ownership')return '#5b53e8';
      if(e.kind==='theme')return (byId.get(e.target.id||e.target)||{}).color||'#c7c4bc';
      return strongestBridges.has(e)?'#776ff0':'#9da0a8';})
    .attr('stroke-width',function(e){return e.kind==='ownership' ? 1.8 : e.kind==='theme' ? .7 : .45+1.35*(e.weight||0);})
    .attr('stroke-linecap','round');
  var node=nodeLayer.selectAll('g').data(visible).join('g').attr('tabindex',0).attr('role','button')
    .attr('data-node-type',function(d){return d.type;}).attr('data-parent',function(d){return d.parent||d.label||'';})
    .on('click',function(e,d){e.stopPropagation();if(d.type==='theme')setThemeFocus(d.label);
      else if(d.type==='post'){selected=d;updateGraphState();}})
    .on('keydown',function(e,d){if((e.key==='Enter'||e.key===' ')&&(d.type==='theme'||d.type==='post')){e.preventDefault();this.dispatchEvent(new MouseEvent('click',{bubbles:true}));}});
  node.append('circle').attr('r',function(d){return d.type==='user'?34:d.type==='theme'?18:Math.min(10,4+Math.sqrt(degree.get(d.id)||0));})
    .attr('fill',function(d){return d.type==='user'?'#fff':(d.color||'#9aa0ab');})
    .attr('stroke',function(d){return d.type==='user'?'#5b53e8':'#fff';}).attr('stroke-width',function(d){return d.type==='user'?4:2;});
  node.filter(function(d){return d.type!=='post';}).append('text').attr('text-anchor','middle')
    .attr('y',function(d){return d.type==='user'?4:-27;}).attr('font-size',function(d){return d.type==='user'?14:(w<500?9:11);})
    .attr('font-weight',700).attr('fill','#191a1e').text(function(d){return d.label;});
  node.append('title').text(function(d){return d.label;});
  function edgeId(v){return typeof v==='object'?v.id:v;}
  function keepEdge(e){return edgesOn&&(e.kind!=='similarity'||(e.weight||0)>=threshold);}
  function curve(e){var dx=e.target.x-e.source.x,dy=e.target.y-e.source.y,mx=(e.source.x+e.target.x)/2;
    var my=(e.source.y+e.target.y)/2,bend=e.kind==='similarity' ? .12 : 0;
    return 'M'+e.source.x+','+e.source.y+' Q'+(mx-dy*bend)+','+(my+dx*bend)+' '+e.target.x+','+e.target.y;}
  function focusContext(){var nodes=new Set(),edges=new Set();if(!focusParent)return {nodes:nodes,edges:edges};
    var theme=themes.find(function(n){return n.label===focusParent;});nodes.add('user:me');if(theme)nodes.add(theme.id);
    visible.forEach(function(n){if(n.type==='post'&&n.parent===focusParent)nodes.add(n.id);});
    links.forEach(function(e){var a=edgeId(e.source),b=edgeId(e.target),an=byId.get(a),bn=byId.get(b);
      if((e.kind==='theme'&&nodes.has(a)&&nodes.has(b))||(e.kind==='ownership'&&theme&&(a===theme.id||b===theme.id))||
        (e.kind==='similarity'&&an&&bn&&an.parent===focusParent&&bn.parent===focusParent)){edges.add(e);}
      if(strongestBridges.has(e)&&keepEdge(e)&&an&&bn&&((an.parent===focusParent)!==(bn.parent===focusParent))){
        edges.add(e);var outside=an.parent===focusParent?bn:an;nodes.add(a);nodes.add(b);
        var outsideTheme=themeFor.get(outside.id),outsideThemeEdge=themeEdgeForPost.get(outside.id);
        if(outsideTheme){nodes.add(outsideTheme);if(outsideThemeEdge)edges.add(outsideThemeEdge);}}
    });return {nodes:nodes,edges:edges};}
  function renderGraphState(){
    var active=new Set(), activeEdges=new Set(), context=focusContext();
    if(selected){active.add(selected.id);var outsideFocus=focusParent&&selected.parent!==focusParent;
      var theme=themeFor.get(selected.id);if(!outsideFocus&&theme)active.add(theme);if(!outsideFocus)active.add('user:me');
      links.forEach(function(e){if(e.kind==='similarity'&&(edgeId(e.source)===selected.id||edgeId(e.target)===selected.id)){
        if(!outsideFocus){active.add(edgeId(e.source));active.add(edgeId(e.target));activeEdges.add(e);}}
        if((e.kind==='theme'||e.kind==='ownership')&&active.has(edgeId(e.source))&&active.has(edgeId(e.target)))activeEdges.add(e);});}
    function nodeOpacity(d){if(searchQuery&&d.type==='post'&&(d.label||'').toLowerCase().indexOf(searchQuery)<0)return .06;
      if(selected&&active.has(d.id))return 1;if(focusParent)return context.nodes.has(d.id)?(selected?.34:1):.07;return selected?.16:1;}
    function haloOpacity(d){if(focusParent&&d.label!==focusParent)return .08;if(selected&&!active.has(d.id))return .3;return 1;}
    function edgeOpacity(e){if(!keepEdge(e))return 0;if(selected&&activeEdges.has(e))return .9;
      if(focusParent)return context.edges.has(e)?(selected?.3:.9):.018;
      if(selected)return .025;return e.kind==='ownership' ? .65 : e.kind==='theme' ? .16 : .25;}
    node.style('opacity',nodeOpacity).select('circle').attr('stroke-width',function(d){return selected&&active.has(d.id)?4:(d.type==='user'?4:2);});
    halo.style('opacity',haloOpacity);path.style('opacity',edgeOpacity);
    var panel=document.getElementById('graph-preview');
    if(!selected){panel.innerHTML='<div class="graph-preview-empty"><div class="graph-user-mini">You</div><div class="graph-preview-title">Your knowledge graph</div><p class="muted">Every theme belongs to one connected personal library. Select a bookmark to trace it through its theme hub back to you, then inspect its closest semantic neighbors across the graph.</p></div>';return;}
    panel.textContent='';var themeNode=byId.get(themeFor.get(selected.id));
    var head=document.createElement('div');head.className='graph-panel-head';
    var themeLabel=document.createElement('span');themeLabel.className='graph-panel-theme';
    var swatch=document.createElement('i');swatch.style.setProperty('--c',(themeNode&&themeNode.color)||'#9aa0ab');
    themeLabel.appendChild(swatch);themeLabel.appendChild(document.createTextNode((themeNode&&themeNode.label)||'Other'));
    var close=document.createElement('button');close.type='button';close.textContent='×';close.setAttribute('aria-label','Close preview');
    close.onclick=function(){selected=null;updateGraphState();};head.appendChild(themeLabel);head.appendChild(close);panel.appendChild(head);
    var crumb=document.createElement('div');crumb.className='graph-path';crumb.textContent='Your library → '+(themeNode?themeNode.label:'Other');panel.appendChild(crumb);
    var title=document.createElement('h3');title.textContent=selected.label||'Saved bookmark';panel.appendChild(title);
    if(selected.url&&/^https?:\/\//i.test(selected.url)){var a=document.createElement('a');a.href=selected.url;a.target='_blank';a.rel='noopener';a.textContent='Open original ↗';panel.appendChild(a);}
    var pathCard=document.createElement('div');pathCard.className='graph-path-card';pathCard.textContent='Bookmark → '+((themeNode&&themeNode.label)||'Theme')+' hub → You';panel.appendChild(pathCard);
    var neighborLinks=links.filter(function(e){return e.kind==='similarity'&&(edgeId(e.source)===selected.id||edgeId(e.target)===selected.id);})
      .sort(function(a,b){return (b.weight||0)-(a.weight||0);});
    var metrics=document.createElement('div');metrics.className='graph-metrics';
    metrics.innerHTML='<div class="graph-metric"><small>Theme fit</small><b>'+((neighborLinks[0]&&neighborLinks[0].weight)||0).toFixed(2)+'</b></div><div class="graph-metric"><small>Neighbors</small><b>'+neighborLinks.length+'</b></div>';panel.appendChild(metrics);
    var related=document.createElement('div');related.className='graph-neighbors';var relatedTitle=document.createElement('h4');relatedTitle.textContent='Nearest semantic neighbors';related.appendChild(relatedTitle);
    neighborLinks.slice(0,5).forEach(function(e){var id=edgeId(e.source)===selected.id?edgeId(e.target):edgeId(e.source),n=byId.get(id);if(!n)return;var row=document.createElement('div');row.className='graph-neighbor';var dot=document.createElement('i');dot.style.setProperty('--c',n.color||'#9aa0ab');var text=document.createElement('span');text.textContent=n.label;row.appendChild(dot);row.appendChild(text);related.appendChild(row);});panel.appendChild(related);
  }
  force.on('tick',function(){
    halo.attr('cx',function(d){return d.x;}).attr('cy',function(d){return d.y;});
    node.attr('transform',function(d){return 'translate('+d.x+','+d.y+')';});path.attr('d',curve);
  });
  function centerOnMe(animated){var t=d3.zoomIdentity.translate(w/2,h/2).scale(1).translate(-root.x,-root.y);
    (animated?svg.transition().duration(450):svg).call(zoom.transform,t);}
  var legendChips=document.querySelectorAll('.graph-legend[data-mode="graph"] .chip');
  function syncLegend(){legendChips.forEach(function(chip){var on=(chip.dataset.parent||'')===(focusParent||'');
    chip.classList.toggle('active',on);chip.setAttribute('aria-pressed',String(on));});}
  function updateGraphState(){syncLegend();renderStats();renderGraphState();}
  function themeAvailable(parent){return themes.some(function(theme){return theme.label===parent;});}
  function setThemeFocus(parent){if(parent&&!themeAvailable(parent))return false;
    focusParent=parent&&focusParent!==parent?parent:null;selected=null;updateGraphState();return true;}
  function disableUnavailableThemes(){legendChips.forEach(function(chip){var parent=chip.dataset.parent||null;
    var unavailable=Boolean(parent&&!themeAvailable(parent));chip.classList.toggle('unavailable',unavailable);
    chip.setAttribute('aria-disabled',String(unavailable));if(unavailable)chip.setAttribute('tabindex','-1');else chip.removeAttribute('tabindex');});}
  legendChips.forEach(function(chip){chip.addEventListener('click',function(e){e.preventDefault();
    if(chip.getAttribute('aria-disabled')==='true')return;setThemeFocus(chip.dataset.parent||null);});});
  function reset(){threshold=.5;document.getElementById('graph-threshold').value=50;
    document.getElementById('graph-threshold-value').textContent='0.50';searchQuery='';
    document.getElementById('graph-search').value='';edgesOn=true;document.getElementById('graph-edges').classList.add('on');
    document.getElementById('graph-edges').setAttribute('aria-pressed','true');setThemeFocus(null);centerOnMe(true);force.alpha(1).restart();}
  document.getElementById('graph-centered').onclick=function(){centered=true;radial();this.classList.add('on');document.getElementById('graph-free').classList.remove('on');force.alpha(.8).restart();};
  document.getElementById('graph-free').onclick=function(){centered=false;themes.forEach(function(n){n.fx=null;n.fy=null;});root.fx=w/2;root.fy=h/2;this.classList.add('on');document.getElementById('graph-centered').classList.remove('on');force.alpha(1).restart();};
  document.getElementById('graph-threshold').oninput=function(){threshold=Number(this.value)/100;document.getElementById('graph-threshold-value').textContent=threshold.toFixed(2);renderGraphState();};
  document.getElementById('graph-edges').onclick=function(){edgesOn=!edgesOn;this.classList.toggle('on',edgesOn);this.setAttribute('aria-pressed',String(edgesOn));renderGraphState();};
  document.getElementById('graph-reset').onclick=reset;document.getElementById('graph-center').onclick=function(){centerOnMe(true);};
  document.getElementById('graph-zoom-in').onclick=function(){svg.transition().call(zoom.scaleBy,1.3);};
  document.getElementById('graph-zoom-out').onclick=function(){svg.transition().call(zoom.scaleBy,.77);};document.getElementById('graph-fit').onclick=function(){centerOnMe(true);};
  function reconcileSearchFocus(){if(!searchQuery||!focusParent)return false;var matches=visible.filter(function(n){return n.type==='post'&&(n.label||'').toLowerCase().indexOf(searchQuery)>=0;});
    if(matches.some(function(n){return n.parent!==focusParent;})){setThemeFocus(null);return true;}return false;}
  document.getElementById('graph-search').oninput=function(){searchQuery=this.value.trim().toLowerCase();if(!reconcileSearchFocus())updateGraphState();};
  svg.on('click',function(e){if(e.target===svg.node()&&(focusParent||selected))setThemeFocus(null);});
  document.addEventListener('keydown',function(e){if(e.key==='Escape'&&(focusParent||selected))setThemeFocus(null);});
  window.addEventListener('resize',function(){if(centered)radial();else{w=svg.node().clientWidth;h=svg.node().clientHeight;root.fx=w/2;root.fy=h/2;}force.alpha(.4).restart();});
  fallback.hidden=true;disableUnavailableThemes();updateGraphState();centerOnMe(false);
}).catch(function(){fallback.hidden=false;});
})();</script>
"""


def _safe_http_url(value: Any) -> str | None:
    """Return a renderable absolute HTTP(S) URL, rejecting malformed and non-string values."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or any(character.isspace() for character in value):
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            return None
    except ValueError:
        return None
    return value


def _avatar_src(url: Any) -> str | None:
    # Bump X's 48px "_normal" avatar to the 73px "_bigger" for crisp retina display.
    safe = _safe_http_url(url)
    return safe.replace("_normal.", "_bigger.") if safe else None


def _media_imgs(media_json: Any) -> str:
    if not media_json:
        return ""
    try:
        media = json.loads(media_json) if isinstance(media_json, str) else media_json
    except (ValueError, TypeError):
        return ""
    imgs = ""
    for item in media:
        if not isinstance(item, dict):
            continue
        url = _safe_http_url(item.get("url"))
        if url:
            imgs += (f'<a href="{esc(url)}" target="_blank" rel="noopener">'
                     f'<img class="media" src="{esc(url)}" '
                     f'alt="{esc(item.get("alt_text") or "")}" loading="lazy"></a>')
    return f'<div class="media-row">{imgs}</div>' if imgs else ""


def post_card(p: dict[str, Any]) -> str:
    text = esc(p.get("text") or "")
    raw_handle = p.get("handle")
    handle = raw_handle.strip() if isinstance(raw_handle, str) else ""
    url = _safe_http_url(p.get("url"))
    raw_source = p.get("source")
    source = raw_source if isinstance(raw_source, str) and raw_source else "x"
    color = parent_color(p.get("parent"))
    tint = (
        f' style="background:color-mix(in srgb,{color} 11%,var(--panel));'
        f'border-color:color-mix(in srgb,{color} 32%,var(--line))"'
        if color
        else ""
    )
    avatar = _avatar_src(p.get("avatar_url"))
    av = (
        f'<img class="avatar" src="{esc(avatar)}" alt="" loading="lazy">'
        if avatar
        else '<div class="avatar"></div>'
    )
    author_base = (_SOURCE_META.get(source) or {}).get("author_base")
    if handle and author_base:
        author_url = author_base + quote(handle, safe="")
        at = (f'<a class="handle" href="{esc(author_url)}" target="_blank" '
              f'rel="noopener">@{esc(handle)}</a>')
    elif source == "browser" and url:
        # Author-less post (browser bookmark): the site's domain is the closest thing to a byline.
        domain = esc((urlsplit(url).hostname or "").lower().removeprefix("www."))
        at = (f'<a class="handle" href="{esc(url)}" target="_blank" '
              f'rel="noopener">🌐 {domain}</a>')
    else:
        at = ""
    badge = esc(_SOURCE_LABELS.get(source, source.capitalize()))
    head = f'<div class="head">{av}{at}<span class="badge">{badge}</span></div>'
    media = _media_imgs(p.get("media_json"))
    link = f'<a href="{esc(url)}" target="_blank" rel="noopener">open ↗</a>' if url else ""
    score = f'score {p["score"]:.2f} · ' if isinstance(p.get("score"), (int, float)) else ""
    meta = f'<div class="meta">{score}{link}</div>' if (link or score) else ""
    return f'<div class="post"{tint}>{head}<div class="body">{text}</div>{media}{meta}</div>'
