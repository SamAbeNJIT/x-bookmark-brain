"""HTML screens for the local web app (issues #4–#7 UI layer).

Server-rendered pages on top of the same tested logic the JSON API uses, wired through the
same `get_db` / `get_ai` dependencies. Kept in its own router so it barely touches web.py.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse

from . import categorize, jobs
from .ask import ask
from .deps import get_ai, get_db
from .search import search
from .templates import esc, legend, page, parent_color, post_card

ui_router = APIRouter()


@ui_router.get("/")
def home(con=Depends(get_db)):
    posts = con.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
    cats = con.execute("SELECT COUNT(*) FROM categories").fetchone()[0]
    embedded = con.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
    body = (
        '<div class="stats">'
        f'<div class="stat"><b>{posts:,}</b> bookmarks</div>'
        f'<div class="stat"><b>{cats}</b> categories</div>'
        f'<div class="stat"><b>{embedded:,}</b> embedded</div>'
        "</div>"
        "<p class=lead>Find a saved post by "
        "<a href='/ui/search'>searching by meaning</a>, "
        "<a href='/ui/ask'>asking a question</a>, or "
        "<a href='/ui/categories'>browsing by category</a>.</p>"
        '<form method=post action="/ui/refresh">'
        "<button>↻ Sync new bookmarks</button> "
        "<span class=muted>pulls, embeds &amp; labels anything new</span></form>"
    )
    return page("Your bookmark brain", body)


@ui_router.post("/ui/refresh")
def ui_refresh_start():
    jobs.start()
    return RedirectResponse(url="/ui/refresh", status_code=303)


@ui_router.get("/ui/refresh")
def ui_refresh():
    s = jobs.status()
    running = s["running"]
    if s["error"]:
        state = f'<div class="answer" style="border-left-color:#d64545">⚠️ {esc(s["error"])}</div>'
    elif s["step"] == "done":
        state = f'<div class="answer">✅ {esc(s["detail"])}</div>'
    elif running:
        state = (
            f'<div class="answer">⏳ <b>{esc(s["step"])}</b> — {esc(s["detail"])}'
            "<br><span class=muted>This page refreshes automatically…</span></div>"
            "<script>setTimeout(function(){location.reload()},3000)</script>"
        )
    else:
        state = '<p class=lead>Pull, embed and label any bookmarks added since the last sync.</p>'

    btn_label = "Syncing…" if running else "↻ Sync now"
    disabled = " disabled" if running else ""
    form = (
        f'<form method=post action="/ui/refresh"><button{disabled}>{btn_label}</button></form>'
    )
    note = (
        "<p class=muted style='margin-top:1.4rem'>Incremental — it stops as soon as it "
        "reaches bookmarks already synced, so it only fetches what's new. New posts cost "
        "a fraction of a cent each to embed and label.</p>"
    )
    return page("Sync", state + form + note)


@ui_router.get("/ui/search")
def ui_search(q: str = "", con=Depends(get_db), ai=Depends(get_ai)):
    form = (
        f'<form method=get action="/ui/search" class="narrow">'
        f'<input type=search name=q value="{esc(q)}" '
        f'placeholder="Search your bookmarks…" autofocus></form>'
    )
    results = ""
    if q:
        hits = search(con, ai, q, 24)
        results = (
            f'<div class="cards">{"".join(post_card(p) for p in hits)}</div>'
            if hits
            else "<p class=muted>No matches.</p>"
        )
    return page("Search", form + results)


def _ask_form(question: str, autofocus: bool = True) -> str:
    af = " autofocus" if autofocus else ""
    return (
        '<form method=post action="/ui/ask" id="askform">'
        f'<textarea name=question rows=3 '
        f'placeholder="Ask a question about your bookmarks…"{af}>{esc(question)}</textarea>'
        '<div class=row style="margin-top:.55rem">'
        '<button id="askbtn">Ask</button>'
        '<span class=muted style="font-size:.82rem">⌘/Ctrl + Enter to send</span>'
        '<span id="thinking" class="thinking" hidden>'
        '<span class="spinner"></span> Thinking… retrieving posts &amp; writing an answer</span>'
        "</div></form>"
        "<script>var _f=document.getElementById('askform');"
        "if(_f){_f.addEventListener('submit',function(){"
        "var b=document.getElementById('askbtn');b.disabled=true;b.textContent='Thinking…';"
        "var t=document.getElementById('thinking');if(t)t.hidden=false;});"
        "var _ta=_f.querySelector('textarea');"
        "if(_ta)_ta.addEventListener('keydown',function(e){"
        "if((e.metaKey||e.ctrlKey)&&e.key==='Enter'){e.preventDefault();_f.requestSubmit();}});}</script>"
    )


@ui_router.get("/ui/ask")
def ui_ask(question: str = "", con=Depends(get_db), ai=Depends(get_ai)):
    return page("Ask", _ask_form(question))


@ui_router.post("/ui/ask")
def ui_ask_post(question: str = Form(...), con=Depends(get_db), ai=Depends(get_ai)):
    result = ask(con, ai, question, 8)
    cited = {c for c in result["citations"]}
    cards = "".join(post_card(p) for p in result["retrieved"] if p["id"] in cited)
    form = _ask_form(question, autofocus=False)
    answer = f'<div class="answer">{esc(result.get("answer") or "")}</div>'
    sources = f'<h3>Cited bookmarks</h3><div class="cards">{cards}</div>' if cards else ""
    return page("Ask", form + answer + sources)


@ui_router.get("/ui/categories")
def ui_categories(con=Depends(get_db)):
    tree = categorize.category_tree(con)
    if not tree:
        body = (
            "<p class=muted>No categories yet. Build one on the "
            "<a href='/ui/taxonomy'>taxonomy</a> page.</p>"
        )
        return page("Categories", body)

    blocks = []
    for i, group in enumerate(tree):
        color = parent_color(group["parent"]) or "#9aa0ab"
        children = "".join(
            f'<a class="child" href="/ui/categories/{c["id"]}">'
            f'<span class="grow">{esc(c["name"])}</span>'
            f'<span class="badge">{c["count"]:,}</span></a>'
            for c in group["children"]
        )
        # First group open by default so the page doesn't look empty.
        open_attr = " open" if i == 0 else ""
        blocks.append(
            f'<details{open_attr}><summary style="--c:{color}">'
            f'<span class="caret">▶</span><span class="sw"></span>'
            f'<span class="grow">{esc(group["parent"])}</span>'
            f'<span class="badge">{group["total"]:,}</span></summary>'
            f'<div class="children">{children}</div></details>'
        )
    groups = [(g["parent"], g["total"]) for g in tree]
    body = '<p class=lead>Each topic has a color. Tap one to see just those tweets, or expand a group below.</p>'
    body += legend(groups)
    body += f'<div class="tree">{"".join(blocks)}</div>'
    return page("Categories", body)


_PAGE = 150


@ui_router.get("/ui/feed")
def ui_feed(parent: str = "", offset: int = 0, partial: int = 0, con=Depends(get_db)):
    active = parent or None
    posts = categorize.feed_posts(con, parent=active, limit=_PAGE, offset=offset)

    # Partial: just the card HTML, for the infinite-scroll appender to insert.
    if partial:
        return HTMLResponse("".join(post_card(p) for p in posts))

    groups = [(g["parent"], g["total"]) for g in categorize.category_tree(con)]
    where = f" in {esc(active)}" if active else ""
    note = f"<p class=lead>Newest first{where} · scroll to keep loading. Tap a color to filter.</p>"
    if not posts:
        return page("Feed", legend(groups, active) + "<p class=muted>No tweets here yet.</p>")

    feed = f'<div id="feed" class="cards">{"".join(post_card(p) for p in posts)}</div>'
    sentinel = '<div id="more" class="muted" style="text-align:center;padding:1.6rem">loading…</div>'
    done = "true" if len(posts) < _PAGE else "false"
    js = (
        "<script>(function(){var off=" + str(_PAGE) + ",busy=false,done=" + done
        + ",parent=" + json.dumps(active or "")
        + ",feed=document.getElementById('feed'),more=document.getElementById('more');"
        "if(done){more.remove();return;}"
        "var io=new IntersectionObserver(function(es){"
        "if(!es[0].isIntersecting||busy||done)return;busy=true;"
        "var u='/ui/feed?partial=1&offset='+off+(parent?'&parent='+encodeURIComponent(parent):'');"
        "fetch(u).then(function(r){return r.text();}).then(function(h){"
        "var n=(h.match(/class=\"post\"/g)||[]).length;"
        "if(n===0){done=true;more.remove();return;}"
        "feed.insertAdjacentHTML('beforeend',h);off+=" + str(_PAGE) + ";busy=false;"
        "if(n<" + str(_PAGE) + "){done=true;more.remove();}"
        "});});io.observe(more);})();</script>"
    )
    return page("Feed", legend(groups, active) + note + feed + sentinel + js)


@ui_router.get("/ui/categories/{category_id}")
def ui_category(category_id: int, con=Depends(get_db)):
    posts = categorize.posts_in_category(con, category_id)
    cards = (
        f'<div class="cards">{"".join(post_card(p) for p in posts)}</div>'
        if posts
        else "<p class=muted>No posts.</p>"
    )
    return page("Category", cards)


@ui_router.get("/ui/taxonomy")
def ui_taxonomy(con=Depends(get_db)):
    cats = categorize.get_taxonomy(con)
    derive = (
        '<form method=post action="/ui/taxonomy/derive">'
        "<button>Derive taxonomy from my bookmarks</button> "
        "<span class=muted>(uses the AI; review/edit below)</span></form>"
    )
    rows = ""
    for c in cats:
        rows += (
            f'<div class="post"><b>{esc(c["name"])}</b> '
            f'<span class=meta>{esc(c.get("definition") or "")}</span>'
            f'<div class=row style="margin-top:.4rem">'
            f'<form method=post action="/ui/taxonomy/{c["id"]}/rename" class=row>'
            f'<input type=text name=name placeholder="rename to…" style="width:200px">'
            f"<button>rename</button></form>"
            f'<form method=post action="/ui/taxonomy/{c["id"]}/delete">'
            f"<button>delete</button></form></div></div>"
        )
    if not cats:
        rows = "<p class=muted>No categories yet — derive a starter set above.</p>"
    return page("Taxonomy", derive + rows)


def _back():
    return RedirectResponse(url="/ui/taxonomy", status_code=303)


@ui_router.post("/ui/taxonomy/derive")
def ui_taxonomy_derive(con=Depends(get_db), ai=Depends(get_ai)):
    proposed = categorize.derive_taxonomy(con, ai)
    categorize.save_taxonomy(con, proposed)
    return _back()


@ui_router.post("/ui/taxonomy/{category_id}/rename")
def ui_taxonomy_rename(category_id: int, name: str = Form(...), con=Depends(get_db)):
    if name.strip():
        categorize.rename_category(con, category_id, name.strip())
    return _back()


@ui_router.post("/ui/taxonomy/{category_id}/delete")
def ui_taxonomy_delete(category_id: int, con=Depends(get_db)):
    categorize.delete_category(con, category_id)
    return _back()
