"""HTML screens for the local web app (issues #4–#7 UI layer).

Server-rendered pages on top of the same tested logic the JSON API uses, wired through the
same `get_db` / `get_ai` dependencies. Kept in its own router so it barely touches web.py.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form
from fastapi.responses import RedirectResponse

from . import categorize, jobs
from .ask import ask
from .deps import get_ai, get_db
from .search import search
from .templates import esc, page, post_card

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
        f'<form method=get action="/ui/search">'
        f'<input type=search name=q value="{esc(q)}" '
        f'placeholder="Search your bookmarks…" autofocus></form>'
    )
    results = ""
    if q:
        hits = search(con, ai, q, 20)
        results = "".join(post_card(p) for p in hits) or "<p class=muted>No matches.</p>"
    return page("Search", form + results)


@ui_router.get("/ui/ask")
def ui_ask(question: str = "", con=Depends(get_db), ai=Depends(get_ai)):
    form = (
        f'<form method=post action="/ui/ask">'
        f'<input type=text name=question value="{esc(question)}" '
        f'placeholder="Ask a question about your bookmarks…" autofocus>'
        f'<div class=row><button>Ask</button></div></form>'
    )
    return page("Ask", form)


@ui_router.post("/ui/ask")
def ui_ask_post(question: str = Form(...), con=Depends(get_db), ai=Depends(get_ai)):
    result = ask(con, ai, question, 8)
    cited = {c for c in result["citations"]}
    cards = "".join(post_card(p) for p in result["retrieved"] if p["id"] in cited)
    form = (
        f'<form method=post action="/ui/ask">'
        f'<input type=text name=question value="{esc(question)}"><div class=row>'
        f'<button>Ask</button></div></form>'
    )
    answer = f'<div class="answer">{esc(result.get("answer") or "")}</div>'
    sources = f"<h3>Cited bookmarks</h3>{cards}" if cards else ""
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
        children = "".join(
            f'<a class="child" href="/ui/categories/{c["id"]}">'
            f'<span class="grow">{esc(c["name"])}</span>'
            f'<span class="badge">{c["count"]:,}</span></a>'
            for c in group["children"]
        )
        # First group open by default so the page doesn't look empty.
        open_attr = " open" if i == 0 else ""
        blocks.append(
            f"<details{open_attr}><summary>"
            f'<span class="caret">▶</span><span class="grow">{esc(group["parent"])}</span>'
            f'<span class="badge">{group["total"]:,}</span></summary>'
            f'<div class="children">{children}</div></details>'
        )
    body = '<p class=lead>Browse by topic — click a group to expand its subcategories.</p>'
    body += f'<div class="tree">{"".join(blocks)}</div>'
    return page("Categories", body)


@ui_router.get("/ui/categories/{category_id}")
def ui_category(category_id: int, con=Depends(get_db)):
    posts = categorize.posts_in_category(con, category_id)
    cards = "".join(post_card(p) for p in posts) or "<p class=muted>No posts.</p>"
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
