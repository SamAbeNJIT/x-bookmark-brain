"""HTML screens for the local web app (issues #4–#7 UI layer).

Server-rendered pages on top of the same tested logic the JSON API uses, wired through the
same `get_db` / `get_ai` dependencies. Kept in its own router so it barely touches web.py.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from . import auth, authui, categorize, credits, jobs, landing, mail, storage, xapi, xauth
from .ask import trim_history
from .config import Config
from .deps import get_ai, get_db, resolve_tenant
from .log import logger
from .search import posts_by_ids, search
from .templates import esc, legend, md_lite, page, parent_color, post_card

ui_router = APIRouter()



@ui_router.get("/")
def home(request: Request, con=Depends(get_db)):
    # Hosted mode: anonymous visitors get the marketing landing page; signed-in users the app.
    cfg = Config.from_env()
    if cfg.require_auth:
        tok = request.cookies.get("xbb_session")
        if not (tok and auth.verify_session_token(tok, cfg.session_secret)):
            return landing.landing_page()
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


@ui_router.get("/oauth/signin")
def oauth_signin(con=Depends(get_db)):
    """Sign in with X (anonymous entry): same PKCE dance as connect, but the 'si_' state
    prefix routes the shared callback into account creation instead of token attach.
    Anonymous request → get_db resolves the DEFAULT tenant, where the verifier is stashed."""
    cfg = Config.from_env()
    if not cfg.x_client_id:
        return page("Sign in", "<p class=muted>X_CLIENT_ID is not set.</p>")
    verifier, challenge = xauth.make_pkce()
    state = "si_" + xauth.make_state()
    storage.set_pkce(con, state, verifier)
    return RedirectResponse(
        xauth.authorize_url(cfg.x_client_id, cfg.x_redirect_uri, state, challenge), status_code=307
    )


def _signin_callback(code: str, state: str, error: str, con) -> RedirectResponse | HTMLResponse:
    """Complete sign-in-with-X: exchange the code, identify the user, find-or-create their
    account, store the (already-granted!) bookmark token under their tenant, set the session,
    auto-start the free-100 sync, and land new accounts on the progress page. (Owner reversed
    the earlier consent-first call on 2026-07-12: signing in with X to a bookmark tool IS the
    consent, and making people find the button was costing conversions.)"""
    if error:
        return authui.login_page(error=f"X sign-in was cancelled ({error}). Try again, or use email.")
    verifier = storage.pop_pkce(con, state)
    if not verifier or not code:
        return authui.login_page(error="Sign-in expired or invalid — please try again.")
    cfg = Config.from_env()
    try:
        tok = xauth.exchange_code(cfg.x_client_id, cfg.x_redirect_uri, code, verifier)
        me = xauth.fetch_me(tok["access_token"])
    except Exception as e:
        return authui.login_page(error=f"X sign-in failed ({type(e).__name__}) — please try again.")
    x_id, handle = str(me["id"]), me.get("username")
    account_id = storage.account_by_x_user_id(con, x_id)
    created = account_id is None
    if created:
        account_id = storage.create_account_from_x(con, x_id, handle)
    logger.info("auth.x_signin tenant=%s handle=@%s created=%s", account_id, handle, created)
    tcon = storage.connect(cfg.app_database_url, account_id)  # token belongs to THEIR tenant
    try:
        xapi.save_tokens(tcon, tok)
    finally:
        tcon.close()
    if created:
        mail.send_owner_alert("🆕 x-bookmarks signup", f"New account via Sign in with X: @{handle}",
                              ses_sender=cfg.ses_sender,
                              owner_email=cfg.owner_alert_email, region=cfg.aws_region)
        jobs.start(account_id)  # free-100 sync starts immediately; /ui/refresh shows progress
    session = auth.make_session_token(account_id, cfg.session_secret)
    resp = RedirectResponse(url="/ui/refresh" if created else "/", status_code=303)
    resp.set_cookie("xbb_session", session, httponly=True, samesite="lax",
                    max_age=auth.SESSION_MAX_AGE_S)
    return resp


@ui_router.get("/oauth/callback")
def oauth_callback(request: Request, code: str = "", state: str = "", error: str = "",
                   con=Depends(get_db)):
    if state.startswith("si_"):  # sign-in-with-X shares the registered redirect URI
        return _signin_callback(code, state, error, con)
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
    try:  # link the X identity so a later "Sign in with X" lands in this same account
        me = xauth.fetch_me(tok["access_token"])
        storage.set_account_x_identity(con, resolve_tenant(request, cfg),
                                       str(me["id"]), me.get("username"))
    except Exception:
        pass  # linking is best-effort; the connect itself already succeeded
    return RedirectResponse(url="/ui/refresh", status_code=303)


@ui_router.post("/ui/refresh")
def ui_refresh_start(request: Request, con=Depends(get_db)):
    cfg = Config.from_env()
    # Import gate: sync is allowed while the account is under its entitlement (free slice +
    # purchased import_limit; None = unlimited). At the cap, send them to the billing slider.
    cap = storage.effective_import_cap(con, cfg.free_bookmark_limit)
    if cap is not None:
        n = con.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
        if n >= cap:
            return RedirectResponse(url="/ui/billing", status_code=303)
    jobs.start(resolve_tenant(request, cfg))  # sync runs under THIS user's tenant
    return RedirectResponse(url="/ui/refresh", status_code=303)


@ui_router.get("/ui/refresh")
def ui_refresh(request: Request, con=Depends(get_db)):
    cfg = Config.from_env()
    s = jobs.status(resolve_tenant(request, cfg))
    running = s["running"]
    # First-run: adapt to HOW they signed up. X sign-in already granted bookmark access
    # (one tap = connected); magic-link users still need the Connect X step first.
    first_run = (not running and s["step"] in ("idle",)
                 and con.execute("SELECT COUNT(*) FROM posts").fetchone()[0] == 0)
    btn_label = "Syncing…" if running else "↻ Sync now"
    if s["error"]:
        state = f'<div class="answer" style="border-left-color:#d64545">⚠️ {esc(s["error"])}</div>'
    elif s["step"] == "done":
        # The conversion moment: their library just got organized — hand them the next step.
        # Cap-hit free users get honest partial-library framing (value first, price later —
        # the first-answer card and banner carry the actual upsell).
        if storage.is_capped_free(con, cfg.free_bookmark_limit):
            logger.info("funnel.upsell_viewed surface=post_sync tenant=%s",
                        resolve_tenant(request, cfg))
            # Only claim "you have more" when a sync PROVED it; an exactly-at-cap library
            # is ambiguous (X has no count API) and gets the hedged copy — trust > punch.
            if storage.library_more_exists(con):
                more = ("You have more bookmarks waiting. Ask your first question, then "
                        "complete your library whenever you're ready.")
            else:
                more = ("If you have more saved posts, complete your library to make them "
                        "searchable. Ask your first question first.")
            lead = (
                f'<div class="answer">✅ Your newest {cfg.free_bookmark_limit} bookmarks '
                "are ready.</div>"
                f'<p class=lead style="margin-top:1rem">{more}</p>'
            )
        else:
            lead = (
                f'<div class="answer">✅ {esc(s["detail"])}</div>'
                '<p class=lead style="margin-top:1rem">Your bookmarks are organized. '
                "Now the fun part:</p>"
            )
        state = (
            lead
            + '<p><a class="stat" style="display:inline-block;text-decoration:none;margin-right:.6rem" '
            'href="/ui/ask"><b style="font-size:1rem">Ask your first question →</b>'
            "<span>“what did I save about…?”</span></a>"
            '<a class="stat" style="display:inline-block;text-decoration:none" '
            'href="/ui/feed"><b style="font-size:1rem">Browse your feed →</b>'
            "<span>color-coded by topic</span></a></p>"
        )
    elif running:
        state = (
            f'<div class="answer">⏳ <b>{esc(s["step"])}</b> — {esc(s["detail"])}'
            "<br><span class=muted>This page refreshes automatically…</span></div>"
            "<script>setTimeout(function(){location.reload()},3000)</script>"
        )
    elif first_run and not xapi.is_connected(con):
        state = (
            f"<p class=lead>Two taps to your organized library: connect your X account, then "
            f"sync your <b>{cfg.free_bookmark_limit} most recent bookmarks — free</b>.</p>"
            '<p><a class="stat" style="display:inline-block;text-decoration:none" '
            'href="/oauth/login"><b style="font-size:1rem">Connect X →</b>'
            "<span>official sign-in, read-only bookmark access</span></a></p>"
        )
        return page("Sync", state)  # no sync button until connected
    elif first_run:
        state = (
            f"<p class=lead>🎉 You're connected. Sync your <b>{cfg.free_bookmark_limit} most "
            f"recent bookmarks — free</b> and watch the AI organize them.</p>"
        )
        btn_label = f"Sync my first {cfg.free_bookmark_limit} bookmarks (free)"
    else:
        state = '<p class=lead>Pull, embed and label any bookmarks added since the last sync.</p>'

    disabled = " disabled" if running else ""
    form = (
        f'<form method=post action="/ui/refresh"><button{disabled}>{btn_label}</button></form>'
    )
    note = (
        "<p class=muted style='margin-top:1.4rem'>Incremental — it stops as soon as it "
        "reaches bookmarks already synced, so it only fetches what's new. Beyond your "
        "free slice, imported bookmarks are 1¢ each.</p>"
    )
    return page("Sync", state + form + note)


def _capped_banner(con, cfg: Config, surface: str) -> str:
    """Slim persistent strip for cap-hit free accounts on Ask/Feed: the library is partial,
    here's how to complete it. Predicate-driven (storage.is_capped_free), so any purchase
    hides it everywhere immediately. Returns '' for everyone else."""
    if not storage.is_capped_free(con, cfg.free_bookmark_limit):
        return ""
    logger.info("funnel.upsell_viewed surface=%s tenant=%s", surface,
                con.execute("SELECT current_setting('app.current_tenant', true)").fetchone()[0])
    return (
        '<div style="background:var(--accent-soft);border-left:3px solid var(--accent);'
        'border-radius:10px;padding:.55rem .9rem;margin-bottom:1rem;font-size:.9rem;'
        'color:var(--accent-ink)">'
        f"Searching your newest {cfg.free_bookmark_limit} bookmarks. Complete your library "
        "to search everything you've saved. "
        f'<a href="/ui/complete-library?src={surface}" style="font-weight:700">'
        "Complete library →</a></div>"
    )


def _first_answer_card(cfg: Config, more_exists: bool, surface: str = "first_answer") -> str:
    """The one-time upgrade card beneath a cap-hit user's FIRST successful answer — the
    moment value was just demonstrated. Non-blocking, no modal, no JS. The middle line
    only asserts "you have more" when a sync proved it (see storage.library_more_exists)."""
    middle = ("You have more saved posts that are not searchable yet." if more_exists
              else "If you have more saved posts, they are not searchable yet.")
    return (
        '<div class="answer" style="border-left:4px solid var(--accent);margin-top:1rem">'
        f"<b>That answer searched your newest {cfg.free_bookmark_limit} bookmarks.</b><br>"
        f"{middle}<br>"
        "Complete your library for 1¢ per bookmark, starting at $3.<br>"
        f'<a class="stat" style="display:inline-block;text-decoration:none;margin-top:.6rem" '
        f'href="/ui/complete-library?src={surface}">'
        '<b style="font-size:1rem">Complete my library →</b></a></div>'
    )


@ui_router.get("/ui/complete-library")
def ui_complete_library(request: Request, src: str = "unknown", con=Depends(get_db)):
    """Single chokepoint for every upsell CTA — the click event can't be missed or
    double-counted, and the billing page gets the source for its context block."""
    cfg = Config.from_env()
    src = "".join(c for c in src if c.isalnum() or c == "_")[:32] or "unknown"
    logger.info("funnel.complete_library_clicked src=%s tenant=%s",
                src, resolve_tenant(request, cfg))
    return RedirectResponse(url=f"/ui/billing?src={src}", status_code=303)


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


def _ask_form(question: str, autofocus: bool = True, history: list | None = None,
              sources: list | None = None) -> str:
    """The ask box. The conversation thread travels in the hidden `history` field — the
    server stays stateless; each POST carries the turns so far (bounded in ask.trim_history).
    `sources` rides the same way (ids only): earlier turns' retrieved/cited bookmarks, so
    follow-up answers keep showing them (bounded in trim_sources)."""
    af = " autofocus" if autofocus else ""
    in_thread = bool(history)
    placeholder = "Ask a follow-up…" if in_thread else "Ask a question about your bookmarks…"
    hist_field = (
        f'<input type=hidden name=history value="{esc(json.dumps(history))}">' if history else ""
    )
    src_field = (
        f'<input type=hidden name=sources value="{esc(json.dumps(sources))}">' if sources else ""
    )
    new_convo = (
        '<a href="/ui/ask" class=muted style="font-size:.82rem">↺ new conversation</a>'
        if in_thread else ""
    )
    return (
        '<form method=post action="/ui/ask" id="askform">'
        + hist_field + src_field
        + f'<textarea name=question rows={2 if in_thread else 3} placeholder="{placeholder}"{af}>'
        f"{esc(question) if not in_thread else ''}</textarea>"
        '<div class=row style="margin-top:.55rem">'
        f'<button id="askbtn">{"Send" if in_thread else "Ask"}</button>'
        '<span class=muted style="font-size:.82rem">⌘/Ctrl + Enter to send</span>'
        + new_convo +
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


def _thread_html(history: list[dict[str, str]]) -> str:
    """Render prior turns as a compact thread above the latest answer."""
    if not history:
        return ""
    bubbles = []
    for t in history:
        if t["role"] == "user":
            bubbles.append(_question_card(t["content"]))
        else:
            bubbles.append(f'<div class="answer">{md_lite(t["content"])}</div>')
    return '<div class="thread">' + "".join(bubbles) + "</div>"


def _question_card(text: str, anchor: bool = False) -> str:
    """A question in the thread gets its own accent-tinted card (owner: plain text got lost
    while scrolling between answer cards). `anchor` marks the latest question so JS can snap
    the thread scroll to it when its answer arrives."""
    aid = ' id="latestq"' if anchor else ""
    return (f'<div{aid} class="answer" style="background:var(--accent-soft);'
            f'color:var(--accent-ink);border-radius:12px;padding:.7rem 1rem;'
            f'margin:1.6rem 0 .5rem;font-weight:600">{esc(text)}</div>')


# Earlier turns' sources ride a hidden form field as id lists (client-held, like `history`).
# Bounds mirror ask.HISTORY_MAX_TURNS = 6 (3 exchanges): 3 prior groups, each capped in ids.
SOURCES_MAX_GROUPS = 3
SOURCES_MAX_IDS = 60


def trim_sources(raw: object) -> list[dict]:
    """Validate + bound the client-supplied source groups (the field is client-editable by
    design — same trust model as `history`). Keeps the newest SOURCES_MAX_GROUPS groups."""
    if not isinstance(raw, list):
        return []
    groups = []
    for g in raw:
        if not isinstance(g, dict) or not isinstance(g.get("q"), str):
            continue
        ids = [str(i) for i in g.get("ids") or [] if isinstance(i, (str, int))]
        ids = ids[:SOURCES_MAX_IDS]
        known = set(ids)
        cited = [str(i) for i in g.get("cited") or [] if str(i) in known]
        if ids:
            groups.append({"q": g["q"][:300], "ids": ids, "cited": cited})
    return groups[:SOURCES_MAX_GROUPS]


def merge_sources(prior: list[dict], question: str,
                  retrieved_ids: list[str], cited_ids: list[str]) -> list[dict]:
    """Prepend this turn's source group, newest first. A post retrieved again this turn is
    dropped from the earlier group it was in — the sources pane accumulates across the thread
    without ever showing the same bookmark twice."""
    new_ids = [str(i) for i in retrieved_ids][:SOURCES_MAX_IDS]
    new_set = set(new_ids)
    kept = []
    for g in prior:
        ids = [i for i in g["ids"] if i not in new_set]
        if ids:
            remaining = set(ids)
            kept.append({"q": g["q"], "ids": ids,
                         "cited": [i for i in g["cited"] if i in remaining]})
    new_group = {"q": question[:300], "ids": new_ids,
                 "cited": [str(i) for i in cited_ids if str(i) in new_set]}
    return [new_group] + kept


def _cited_card(p: dict, cited: set[str], refno: dict[str, int],
                ans_text: str | None = None) -> str:
    """A post card, with a `★ cited [n]` badge when the answer used it. `ans_text` (latest
    turn only) gates the [n] suffix to markers that actually appear in the prose."""
    html = post_card(p)
    if str(p["id"]) in cited:
        n = refno.get(str(p["id"]))
        label = f"★ cited [{n}]" if n and (ans_text is None or f"[{n}]" in ans_text) else "★ cited"
        html = html.replace(
            '<div class="head">',
            '<div class="head"><span class="badge" '
            f'style="background:var(--accent-soft);color:var(--accent-ink)">{label}</span>',
            1,
        )
    return html


@ui_router.get("/ui/ask")
def ui_ask(question: str = "", con=Depends(get_db), ai=Depends(get_ai)):
    banner = _capped_banner(con, Config.from_env(), "banner_ask")
    return page("Ask", banner + _ask_form(question))


@ui_router.post("/ui/ask")
def ui_ask_post(request: Request, question: str = Form(...), history: str = Form(""),
                sources: str = Form(""), con=Depends(get_db), ai=Depends(get_ai)):
    # The thread rides in hidden form fields (client-held state, server stays stateless);
    # trim_history/trim_sources both validate the client-editable JSON and bound it.
    try:
        turns = trim_history(json.loads(history)) if history else []
    except (json.JSONDecodeError, TypeError):
        turns = []
    try:
        prior_sources = trim_sources(json.loads(sources)) if sources else []
    except (json.JSONDecodeError, TypeError):
        prior_sources = []
    form = _ask_form(question, autofocus=False, history=turns or None,
                     sources=prior_sources or None)
    cfg = Config.from_env()
    # Owner perk: deeper retrieval against the 17k corpus.
    k = 50 if (cfg.owner_tenant_id
               and resolve_tenant(request, cfg) == cfg.owner_tenant_id) else 30
    try:
        result = credits.ask_charged(con, ai, question, k, cfg.ask_price_usd,
                                     cfg.free_asks_per_day, history=turns)
    except credits.OutOfCredits:
        msg = (f'<div class="answer">You\'ve used today\'s {cfg.free_asks_per_day} free questions '
               f'and your credit balance is empty. <a href="/ui/billing">Top up</a> '
               f'(${cfg.ask_price_usd:.2f}/question) or come back tomorrow for '
               f'{cfg.free_asks_per_day} more free ones.</div>')
        return page("Ask", form + msg)
    except Exception:
        return page("Ask", form + _ai_error("Ask"))
    cited = set(result["citations"])
    retrieved = result["retrieved"]
    # Raw post ids leaking into the prose read as garbage numbers (owner report). The prompt
    # now forbids them, but models regress: swap any retrieved id in the text for a numbered
    # [n] marker and number the matching card's badge so the reference points somewhere.
    refno = {pid: i + 1 for i, pid in enumerate(result["citations"])}
    ans_text = result.get("answer") or ""
    for p in retrieved:
        pid = str(p.get("id") or "")
        if pid and pid in ans_text:
            n = refno.setdefault(pid, len(refno) + 1)
            ans_text = ans_text.replace(f"({pid})", f"[{n}]").replace(pid, f"[{n}]")
    # Next form carries the thread + accumulated sources including this exchange.
    new_turns = turns + [{"role": "user", "content": question},
                         {"role": "assistant", "content": ans_text}]
    groups = merge_sources(prior_sources, question,
                           [str(p["id"]) for p in retrieved], result["citations"])
    form = _ask_form("", autofocus=False, history=trim_history(new_turns), sources=groups)

    cited_str = {str(c) for c in cited}
    cards = "".join(_cited_card(p, cited_str, refno, ans_text) for p in retrieved)
    # Prior turns render compactly above; the latest question + answer are the main event.
    thread = _thread_html(turns)
    latest_q = _question_card(question, anchor=True)
    answer = f'<div class="answer">{md_lite(ans_text)}</div>'
    # One-time upsell at the moment of demonstrated value: a cap-hit user's FIRST answer.
    if result.get("ask_number") == 1 and storage.is_capped_free(con, cfg.free_bookmark_limit):
        logger.info("funnel.upsell_viewed surface=first_answer first_answer=true tenant=%s",
                    resolve_tenant(request, cfg))
        answer += _first_answer_card(cfg, storage.library_more_exists(con))
    if retrieved:
        # Right pane: this turn's sources on top, earlier turns' below (never replaced —
        # each group under its question). Groups already deduped by merge_sources.
        right = [
            f'<h3>{len(retrieved)} related bookmarks '
            f'<span class=muted>({len(cited)} cited in the answer)</span></h3>'
            f'<div class="cards">{cards}</div>'
        ]
        old_posts = {p["id"]: p for p in
                     posts_by_ids(con, [i for g in groups[1:] for i in g["ids"]])}
        for g in groups[1:]:
            posts = [old_posts[i] for i in g["ids"] if i in old_posts]
            if not posts:
                continue
            grefno = {pid: n + 1 for n, pid in enumerate(g["cited"])}
            gcards = "".join(_cited_card(p, set(g["cited"]), grefno) for p in posts)
            right.append(f'<h3 class="src-group">from: “{esc(g["q"][:120])}”</h3>'
                         f'<div class="cards">{gcards}</div>')
        # Two-pane chat: the thread scrolls in its own column with the composer docked at
        # the bottom; on load, snap the scroll so the newest question sits at the top.
        body = (
            '<div class="ask-cols">'
            '<div class="ask-left">'
            f'<div class="ask-scroll" id="askscroll">{thread}{latest_q}{answer}</div>'
            f'<div class="ask-composer">{form}</div>'
            "</div>"
            f'<div class="ask-right">{"".join(right)}</div>'
            "</div>"
            # Snap: page down to the columns (the sticky pane is viewport-sized once there),
            # then the thread scroll to the newest question, answer below it. Pad the scroll
            # bottom first so a short thread can still put the question at the top.
            "<script>var _ac=document.querySelector('.ask-cols');"
            "if(_ac)window.scrollTo(0,_ac.getBoundingClientRect().top+window.pageYOffset-10);"
            "var _aq=document.getElementById('latestq'),"
            "_as=document.getElementById('askscroll');"
            # (content bottom, not scrollHeight: for a short thread scrollHeight reports the
            # pane height, which under-computes the padding by exactly the empty gap)
            "if(_aq&&_as){var _lc=_as.lastElementChild,"
            "_bot=_lc.offsetTop+_lc.offsetHeight+16,"
            "_pad=_aq.offsetTop-6+_as.clientHeight-_bot;"
            "if(_pad>0)_as.style.paddingBottom=_pad+'px';"
            "_as.scrollTop=Math.max(0,_aq.offsetTop-6);}</script>"
        )
        return page("Ask", body, wide=True, rail=True)
    return page("Ask", thread + latest_q + answer + form)


@ui_router.get("/ui/feedback")
def ui_feedback():
    body = (
        "<p class=lead>Found a bug? Want a feature? Tell us — this goes straight to a human.</p>"
        '<form method=post action="/ui/feedback">'
        '<textarea name="message" required maxlength="4000" rows="6" placeholder="What\'s on your mind?" '
        'style="width:100%;max-width:640px;padding:.8rem 1rem;font-size:1rem;font-family:inherit;'
        'border:1px solid var(--line-2);border-radius:12px;background:var(--panel);box-shadow:var(--shadow)">'
        "</textarea>"
        '<div class=row style="margin-top:.6rem"><button>Send feedback</button></div></form>'
    )
    return page("Feedback", body)


@ui_router.post("/ui/feedback")
def ui_feedback_post(request: Request, message: str = Form(...), con=Depends(get_db)):
    from . import mail
    cfg = Config.from_env()
    tenant = resolve_tenant(request, cfg)
    sender = storage.get_account_email(con, tenant) or tenant
    mail.send_owner_alert("💬 x-bookmarks feedback",
                          f"From: {sender}\n\n{message[:4000]}",
                          ses_sender=cfg.ses_sender,
                          owner_email=cfg.owner_alert_email, region=cfg.aws_region)
    return page("Feedback", '<div class="answer">💌 Got it — thank you! We read every one.</div>'
                            '<p><a href="/ui/feedback">Send another</a></p>')


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
            '<span class="grow">Unsorted</span>'
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
    banner = _capped_banner(con, Config.from_env(), "banner_feed")
    if not posts:
        return page("Feed", banner + legend(groups, active) + "<p class=muted>No tweets here yet.</p>")

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
    return page("Feed", banner + legend(groups, active) + note + feed + sentinel + js)


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
        f"<p class=lead>{shown} posts without a confident category — image-only / bare-link "
        "bookmarks (no text to work with) and ones that didn't clearly fit any topic. "
        "Newest first.</p>"
    )
    cards = (
        f'<div class="cards">{"".join(post_card(p) for p in posts)}</div>'
        if posts
        else "<p class=muted>Nothing unsorted.</p>"
    )
    return page("Unsorted", note + cards)


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
