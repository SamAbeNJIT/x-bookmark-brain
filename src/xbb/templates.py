"""Tiny server-rendered HTML helpers for the local web UI.

No template engine — just escaped f-strings. Keeps the UI dependency-light and the whole
view layer in one readable place.
"""

from __future__ import annotations

import html
import json
import re
from typing import Any
from urllib.parse import quote

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


def parent_color(parent: str | None) -> str | None:
    return PARENT_COLORS.get(parent) if parent else None


def legend(groups: list[tuple[str, int]], active: str | None = None) -> str:
    """Clickable color legend. `groups` is [(parent, count)]; links filter the feed."""
    all_cls = " active" if active is None else ""
    chips = [f'<a class="chip{all_cls}" href="/ui/feed">All</a>']
    for parent, count in groups:
        c = PARENT_COLORS.get(parent, PARENT_COLORS["Other"])
        cls = " active" if parent == active else ""
        chips.append(
            f'<a class="chip{cls}" href="/ui/feed?parent={quote(parent)}" style="--c:{c}">'
            f'<span class="sw"></span>{esc(parent)}'
            f'<span class="badge">{count:,}</span></a>'
        )
    return f'<div class="legend">{"".join(chips)}</div>'

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

  /* ask results: answer keeps a comfortable reading width; tweets absorb ALL remaining space */
  .ask-cols { display: grid; grid-template-columns: minmax(380px, 56ch) 1fr;
              gap: 1.4rem; align-items: start; }
  .ask-left { position: sticky; top: 1rem; max-height: calc(100vh - 2rem); overflow-y: auto; }
  .ask-left .answer { margin: 0; max-width: none; white-space: normal; }
  .ask-right h3 { margin-top: 0; }
  @media (max-width: 900px) { .ask-cols { grid-template-columns: 1fr; }
    .ask-left { position: static; max-height: none; } }

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
    ("/ui/feed", "Feed", "▦"),
    ("/ui/taxonomy", "Taxonomy", "⚙"),
    ("/ui/refresh", "Sync", "↻"),
    ("/ui/billing", "Billing", "◈"),
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
    '<div class="side-foot">private · AI-searched<br>'
    '<a href="/terms" style="color:inherit">terms</a> · '
    '<a href="/privacy" style="color:inherit">privacy</a><br>'
    "not affiliated with X Corp.</div>"
    "</aside>"
)

_ACTIVE_JS = (
    "<script>document.querySelectorAll('.sidebar nav a').forEach(function(a){"
    "var h=a.getAttribute('href');var p=location.pathname;"
    "if(h===p||(h!=='/'&&p.indexOf(h)===0))a.classList.add('active');});</script>"
)

# Row-major masonry: distribute cards (in DOM/chronological order) into the shortest column,
# so the top row is the newest items left-to-right and columns pack with no gaps. Exposes
# window.__masonryAdd(container, html) for the feed's infinite scroll to append more.
# Collapse long tweet bodies to 8 lines with a Show more/less toggle. Runs BEFORE the masonry
# script (order in page()) so column heights are measured on the clamped cards.
_CLAMP_JS = (
    "<script>window.__clampCards=function(root){"
    "(root||document).querySelectorAll('.post .body:not([data-clamped])').forEach(function(b){"
    "b.setAttribute('data-clamped','1');b.classList.add('clamp');"
    "if(b.scrollHeight<=b.clientHeight+2){b.classList.remove('clamp');return;}"
    "var t=document.createElement('a');t.href='javascript:void(0)';t.className='more';"
    "t.textContent='Show more';"
    "t.onclick=function(){var c=b.classList.toggle('clamp');"
    "t.textContent=c?'Show more':'Show less';};"
    "b.after(t);});};window.__clampCards();</script>"
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
    "function layout(){document.querySelectorAll('.cards').forEach(build);}"
    "window.__masonryAdd=function(c,html){if(!c.querySelector('.masonry-col'))build(c);"
    "var mx=0;c.querySelectorAll('.post').forEach(function(x){var o=x.getAttribute('data-ord');if(o!==null)mx=Math.max(mx,+o+1);});"
    "var t=document.createElement('div');t.innerHTML=html;"
    "Array.prototype.slice.call(t.querySelectorAll('.post')).forEach(function(card){"
    "card.setAttribute('data-ord',mx++);shortest(c).appendChild(card);});"
    "if(window.__clampCards)__clampCards(c);};"
    "layout();window.addEventListener('load',layout);"
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


def page(title: str, body: str, wide: bool = False) -> HTMLResponse:
    wrap = "wrap wide" if wide else "wrap"
    return HTMLResponse(
        "<!doctype html><html lang=en><head><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width, initial-scale=1'>"
        f"<title>{esc(title)} · bookmark-brain</title>{_HEAD}{_STYLE}</head>"
        f"<body>{_SIDEBAR}<main class=\"content\"><div class=\"{wrap}\">"
        f"<h1>{esc(title)}</h1>{body}</div></main>{_ACTIVE_JS}{_CLAMP_JS}{_MASONRY_JS}</body></html>"
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
    return f'<div class="post"{tint}>{head}<div class="body">{text}</div>{media}{meta}</div>'
