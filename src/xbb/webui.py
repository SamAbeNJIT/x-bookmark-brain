"""HTML screens for the local web app (issues #4–#7 UI layer).

Server-rendered pages on top of the same tested logic the JSON API uses, wired through the
same `get_db` / `get_ai` dependencies. Kept in its own router so it barely touches web.py.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from . import categorize, credits, jobs, storage, xapi, xauth
from .config import Config
from .deps import get_ai, get_db, resolve_tenant
from .search import search
from .templates import esc, legend, page, parent_color, post_card

ui_router = APIRouter()



@ui_router.get("/")
def home(con=Depends(get_db)):
    posts = con.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
    cats = con.execute("SELECT COUNT(*) FROM categories").fetchone()[0]
    labeled = con.execute("SELECT COUNT(DISTINCT post_id) FROM assignments").fetchone()[0]
    unlabeled = categorize.unlabeled_count(con)

    def tile(n, label, href=None):
        inner = f"<b>{n:,}</b><span>{esc(label)}</span>"
        return (
            f'<a class="stat" href="{href}">{inner}</a>' if href else f'<div class="stat">{inner}</div>'
        )

    body = (
        '<div class="stats">'
        + tile(posts, "bookmarks")
        + tile(cats, "categories", "/ui/categories")
        + tile(labeled, "labeled")
        + tile(unlabeled, "unlabeled", "/ui/unlabeled")
        + "</div>"
        "<p class=lead>Find a saved post by "
        "<a href='/ui/search'>searching by meaning</a>, "
        "<a href='/ui/ask'>asking a question</a>, or "
        "<a href='/ui/categories'>browsing by category</a>.</p>"
    )
    if xapi.is_connected(con):
        body += (
            '<form method=post action="/ui/refresh">'
            "<button>↻ Sync new bookmarks</button> "
            "<span class=muted>✓ connected to X · pulls, embeds &amp; labels anything new</span></form>"
        )
    else:
        body += (
            '<p><a class="stat" style="display:inline-block;text-decoration:none" '
            'href="/oauth/login"><b style="font-size:1rem">Connect X →</b>'
            '<span>authorize read access to your bookmarks</span></a></p>'
        )
    return page("Your bookmark brain", body)


@ui_router.get("/oauth/login")
def oauth_login(con=Depends(get_db)):
    cfg = Config.from_env()
    if not cfg.x_client_id:
        return page("Connect X", "<p class=muted>X_CLIENT_ID is not set in .env.</p>")
    verifier, challenge = xauth.make_pkce()
    state = xauth.make_state()
    storage.set_pkce(con, state, verifier)  # DB-backed: survives across web instances
    return RedirectResponse(
        xauth.authorize_url(cfg.x_client_id, cfg.x_redirect_uri, state, challenge), status_code=307
    )


@ui_router.get("/oauth/callback")
def oauth_callback(code: str = "", state: str = "", error: str = "", con=Depends(get_db)):
    if error:
        return page("Connect X", f'<div class="answer" style="border-left-color:#d64545">X denied the connection: {esc(error)}</div>')
    verifier = storage.pop_pkce(con, state)
    if not verifier or not code:
        return page("Connect X", '<div class="answer" style="border-left-color:#d64545">Connection expired or invalid — start again from Sync.</div>')
    cfg = Config.from_env()
    try:
        tok = xauth.exchange_code(cfg.x_client_id, cfg.x_redirect_uri, code, verifier)
        xapi.save_tokens(con, tok)
    except Exception as e:
        return page("Connect X", f'<div class="answer" style="border-left-color:#d64545">Token exchange failed: {esc(type(e).__name__)}. Check the app settings (Native/public client, redirect URI).</div>')
    return RedirectResponse(url="/ui/refresh", status_code=303)


@ui_router.post("/ui/refresh")
def ui_refresh_start(request: Request, con=Depends(get_db)):
    # Ingestion gate: importing/syncing bookmarks requires the one-time ingestion charge.
    if not storage.is_ingestion_paid(con):
        return RedirectResponse(url="/ui/billing", status_code=303)
    jobs.start(resolve_tenant(request, Config.from_env()))  # sync runs under THIS user's tenant
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
        try:
            hits = search(con, ai, q, 24)
        except Exception:
            return page("Search", form + _ai_error("Search"))
        results = (
            f'<div class="cards">{"".join(post_card(p) for p in hits)}</div>'
            if hits
            else "<p class=muted>No matches.</p>"
        )
    return page("Search", form + results)


def _ai_error(what: str) -> str:
    return (
        '<div class="answer" style="border-left-color:#d64545">'
        f"⚠️ {esc(what)} is temporarily unavailable — the AI service (Amazon Bedrock) "
        "returned an error. Check your AWS credentials/region, then try again.</div>"
    )


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
    form = _ask_form(question, autofocus=False)
    cfg = Config.from_env()
    try:
        result = credits.ask_charged(con, ai, question, 30, cfg.ask_price_usd)
    except credits.OutOfCredits:
        msg = (f'<div class="answer">You\'re out of credits — each question costs '
               f'${cfg.ask_price_usd:.2f}. <a href="/ui/billing">Top up</a> to continue.</div>')
        return page("Ask", form + msg)
    except Exception:
        return page("Ask", form + _ai_error("Ask"))
    cited = set(result["citations"])
    retrieved = result["retrieved"]

    def _card(p):
        html = post_card(p)
        if p["id"] in cited:  # flag the ones the answer actually used
            html = html.replace(
                '<div class="head">',
                '<div class="head"><span class="badge" '
                'style="background:var(--accent-soft);color:var(--accent-ink)">★ cited</span>',
                1,
            )
        return html

    cards = "".join(_card(p) for p in retrieved)
    answer = f'<div class="answer">{esc(result.get("answer") or "")}</div>'
    sources = (
        f'<h3>{len(retrieved)} related bookmarks '
        f'<span class=muted>({len(cited)} cited in the answer)</span></h3>'
        f'<div class="cards">{cards}</div>'
        if retrieved
        else ""
    )
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
    n_unlabeled = categorize.unlabeled_count(con)
    if n_unlabeled:
        body += (
            '<div class="tree"><a class="child" href="/ui/unlabeled" '
            'style="margin-left:0;border-left-color:#9aa0ab">'
            '<span class="grow">Unlabeled</span>'
            f'<span class="badge">{n_unlabeled:,}</span></a></div>'
        )
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
        "if(window.__masonryAdd){window.__masonryAdd(feed,h);}else{feed.insertAdjacentHTML('beforeend',h);}"
        "off+=" + str(_PAGE) + ";busy=false;"
        "if(n<" + str(_PAGE) + "){done=true;more.remove();}"
        "});});io.observe(more);})();</script>"
    )
    return page("Feed", legend(groups, active) + note + feed + sentinel + js)


@ui_router.get("/ui/categories/{category_id}")
def ui_category(category_id: int, con=Depends(get_db)):
    row = con.execute("SELECT name FROM categories WHERE id = %s", (category_id,)).fetchone()
    name = row[0] if row else "Category"
    posts = categorize.posts_in_category(con, category_id)
    head = (
        '<p><a href="/ui/categories">← all categories</a></p>'
        f"<p class=lead>{len(posts):,} bookmarks in this category.</p>"
    )
    cards = (
        f'<div class="cards">{"".join(post_card(p) for p in posts)}</div>'
        if posts
        else "<p class=muted>No posts.</p>"
    )
    return page(name, head + cards)


@ui_router.get("/ui/unlabeled")
def ui_unlabeled(con=Depends(get_db)):
    posts = categorize.posts_unlabeled(con)
    total = categorize.unlabeled_count(con)
    shown = f"first {len(posts):,} of {total:,}" if total > len(posts) else f"{total:,}"
    note = (
        f"<p class=lead>{shown} posts with no category — a mix of image-only / bare-link "
        "bookmarks (no text to work with) and ones the labeler skipped. Newest first.</p>"
    )
    cards = (
        f'<div class="cards">{"".join(post_card(p) for p in posts)}</div>'
        if posts
        else "<p class=muted>Nothing unlabeled.</p>"
    )
    return page("Unlabeled", note + cards)


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
