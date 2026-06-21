"""Tiny server-rendered HTML helpers for the local web UI.

No template engine — just escaped f-strings. Keeps the UI dependency-light and the whole
view layer in one readable place.
"""

from __future__ import annotations

import html
import json
from typing import Any

from fastapi.responses import HTMLResponse

_HEAD = (
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

  /* content */
  .content { margin-left: 224px; flex: 1; padding: 2.4rem clamp(1.2rem, 5vw, 3.5rem) 5rem; }
  .wrap { max-width: 720px; margin: 0 auto; }
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
  input[type=text], input[type=search] { width: 100%; padding: .8rem 1rem; font-size: 1.02rem;
         border: 1px solid var(--line-2); border-radius: 12px; background: var(--panel);
         box-shadow: var(--shadow); font-family: inherit; }
  input:focus { outline: none; border-color: var(--accent);
                box-shadow: 0 0 0 4px var(--accent-soft); }
  button { padding: .6rem 1.05rem; font-size: .92rem; font-weight: 600; border: 0;
           background: var(--accent); color: #fff; border-radius: 11px; cursor: pointer;
           transition: background .14s, transform .1s; font-family: inherit; }
  button:hover { background: var(--accent-ink); }
  button:active { transform: translateY(1px); }
  button.ghost { background: var(--panel); color: var(--muted); border: 1px solid var(--line-2); }
  form { margin: .5rem 0; }
  .row { display: flex; gap: .5rem; align-items: center; flex-wrap: wrap; }

  /* answer */
  .answer { background: linear-gradient(180deg, #fbfbff, var(--panel)); border: 1px solid var(--line);
            border-left: 4px solid var(--accent); padding: 1.1rem 1.2rem; border-radius: 12px;
            margin: 1.2rem 0; box-shadow: var(--shadow); white-space: pre-wrap; font-size: .98rem; }

  /* stats */
  .stats { display: flex; gap: .7rem; flex-wrap: wrap; margin: 0 0 1.4rem; }
  .stat { background: var(--panel); border: 1px solid var(--line); border-radius: 14px;
          padding: .85rem 1.1rem; box-shadow: var(--shadow); min-width: 7rem; }
  .stat b { font-family: var(--display); font-size: 1.5rem; display: block; letter-spacing: -.02em; }
  .stat span { font-size: .82rem; color: var(--muted); }
  .badge { display: inline-block; min-width: 1.4rem; text-align: center; font-size: .76rem;
           font-weight: 600; color: var(--muted); background: #efece4; border-radius: 999px;
           padding: .14rem .55rem; }

  /* category tree */
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

  @media (max-width: 760px) {
    body { flex-direction: column; }
    .sidebar { position: static; width: auto; flex-direction: row; align-items: center;
               padding: .6rem .8rem; gap: .2rem; overflow-x: auto; }
    .brand { padding: .2rem .5rem; font-size: 1.05rem; }
    .sidebar nav { flex-direction: row; }
    .sidebar nav a .ic { display: none; }
    .side-foot { display: none; }
    .content { margin-left: 0; padding: 1.4rem 1.1rem 3rem; }
  }
</style>
"""

_NAV_ITEMS = [
    ("/", "Home", "◆"),
    ("/ui/search", "Search", "⌕"),
    ("/ui/ask", "Ask", "✦"),
    ("/ui/categories", "Categories", "▤"),
    ("/ui/taxonomy", "Taxonomy", "⚙"),
]

_SIDEBAR = (
    '<aside class="sidebar">'
    '<div class="brand">bookmark<span class="dot">.</span><br>brain</div>'
    "<nav>"
    + "".join(
        f'<a href="{href}"><span class="ic">{ic}</span>{label}</a>'
        for href, label, ic in _NAV_ITEMS
    )
    + "</nav>"
    '<div class="side-foot">local · private · AI-searched</div>'
    "</aside>"
)

_ACTIVE_JS = (
    "<script>document.querySelectorAll('.sidebar nav a').forEach(function(a){"
    "var h=a.getAttribute('href');var p=location.pathname;"
    "if(h===p||(h!=='/'&&p.indexOf(h)===0))a.classList.add('active');});</script>"
)


def esc(value: Any) -> str:
    return html.escape(str(value)) if value is not None else ""


def page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(
        "<!doctype html><html lang=en><head><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width, initial-scale=1'>"
        f"<title>{esc(title)} · bookmark-brain</title>{_HEAD}{_STYLE}</head>"
        f"<body>{_SIDEBAR}<main class=\"content\"><div class=\"wrap\">"
        f"<h1>{esc(title)}</h1>{body}</div></main>{_ACTIVE_JS}</body></html>"
    )


def _avatar_src(url: str | None) -> str | None:
    # Bump X's 48px "_normal" avatar to the 73px "_bigger" for crisp retina display.
    return url.replace("_normal.", "_bigger.") if url else url


def _media_imgs(media_json: Any) -> str:
    if not media_json:
        return ""
    try:
        media = json.loads(media_json) if isinstance(media_json, str) else media_json
    except (ValueError, TypeError):
        return ""
    imgs = "".join(
        f'<a href="{esc(m["url"])}" target="_blank" rel="noopener">'
        f'<img class="media" src="{esc(m["url"])}" alt="{esc(m.get("alt_text") or "")}" '
        f'loading="lazy"></a>'
        for m in media
        if isinstance(m, dict) and m.get("url")
    )
    return f'<div class="media-row">{imgs}</div>' if imgs else ""


def post_card(p: dict[str, Any]) -> str:
    text = esc(p.get("text") or "")
    handle = esc(p.get("handle") or "")
    url = p.get("url")
    avatar = _avatar_src(p.get("avatar_url"))
    av = (
        f'<img class="avatar" src="{esc(avatar)}" alt="" loading="lazy">'
        if avatar
        else '<div class="avatar"></div>'
    )
    at = (
        f'<a class="handle" href="https://x.com/{handle}" target="_blank" '
        f'rel="noopener">@{handle}</a>'
        if handle
        else ""
    )
    head = f'<div class="head">{av}{at}</div>'
    media = _media_imgs(p.get("media_json"))
    link = f'<a href="{esc(url)}" target="_blank" rel="noopener">open ↗</a>' if url else ""
    score = f'score {p["score"]:.2f} · ' if isinstance(p.get("score"), (int, float)) else ""
    meta = f'<div class="meta">{score}{link}</div>' if (link or score) else ""
    return f'<div class="post">{head}<div class="body">{text}</div>{media}{meta}</div>'
