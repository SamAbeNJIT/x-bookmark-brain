"""HTML screens for the local web app (issues #4–#7 UI layer).

Server-rendered pages on top of the same tested logic the JSON API uses, wired through the
same `get_db` / `get_ai` dependencies. Kept in its own router so it barely touches web.py.
"""

from __future__ import annotations

import json
import random
from urllib.parse import urlencode

from fastapi import (APIRouter, Depends, File, Form, HTTPException, Request, Response,
                     UploadFile)
from fastapi.responses import HTMLResponse, RedirectResponse

from . import (auth, authui, autoanswer, bookmarks, categorize, credits, ingestion, jobs,
               landing, mail, sources, storage, xapi, xauth, xconv)
from .ask import trim_history
from .config import Config
from .deps import get_ai, get_db, resolve_tenant
from .log import logger
from .search import posts_by_ids, search
from .templates import (_SOURCE_LABELS, esc, graph_visualization, legend, md_lite, page,
                        parent_color, post_card)

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
    body += _source_controls(con, cfg)
    return page("Your bookmark brain", body)


def _source_controls(con, cfg: Config) -> str:
    """Registry-driven Connect/Sync controls for configured non-X OAuth sources."""
    blocks = []
    for adapter in sources.configured_oauth_sources(cfg):
        label = sources.source_label(adapter.name)
        note = ("Reddit exposes only about the newest 1,000 saved items. "
                if adapter.name == "reddit" else "")
        if adapter.is_connected(con):
            blocks.append(
                f'<form method=post action="/ui/refresh/{adapter.name}"><button>↻ Sync {label}</button> '
                f'<span class=muted>✓ connected · {note}unlimited and free</span></form>'
            )
        else:
            blocks.append(
                f'<p><a class="stat" style="display:inline-block;text-decoration:none" '
                f'href="/connect/{adapter.name}/login"><b style="font-size:1rem">Connect {label} →</b>'
                f'<span>{note}read-only saved-item access · unlimited and free</span></a></p>'
            )
    return "".join(blocks)


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


def _signin_callback(request: Request, code: str, state: str, error: str,
                     con) -> RedirectResponse | HTMLResponse:
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
        jobs.start(account_id)  # free-slice sync starts immediately; /ui/refresh shows progress
        # Ad attribution: NEW accounts only (the twclid cookie survives the X OAuth round
        # trip because it's first-party) — repeat sign-ins never fire.
        xconv.fire_registration(cfg, account_id,
                                request.cookies.get(xconv.TWCLID_COOKIE))
    session = auth.make_session_token(account_id, cfg.session_secret)
    resp = RedirectResponse(url="/ui/refresh" if created else "/", status_code=303)
    resp.set_cookie("xbb_session", session, httponly=True, samesite="lax",
                    max_age=auth.SESSION_MAX_AGE_S)
    return resp


@ui_router.get("/oauth/callback")
def oauth_callback(request: Request, code: str = "", state: str = "", error: str = "",
                   con=Depends(get_db)):
    if state.startswith("si_"):  # sign-in-with-X shares the registered redirect URI
        return _signin_callback(request, code, state, error, con)
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


def _oauth_adapter_or_404(source: str, cfg: Config):
    try:
        return sources.get_oauth_adapter(source, cfg)
    except sources.SourceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@ui_router.get("/connect/{source}/login")
def source_oauth_login(source: str, request: Request, con=Depends(get_db)):
    cfg = Config.from_env()
    adapter = _oauth_adapter_or_404(source, cfg)
    tenant_id = resolve_tenant(request, cfg)
    state = sources.make_oauth_state(source, tenant_id, cfg.session_secret)
    return RedirectResponse(adapter.authorize_url(cfg, con, state), status_code=307)


@ui_router.get("/connect/{source}/callback")
def source_oauth_callback(source: str, request: Request, code: str = "", state: str = "",
                          error: str = "", con=Depends(get_db)):
    cfg = Config.from_env()
    adapter = _oauth_adapter_or_404(source, cfg)
    tenant_id = resolve_tenant(request, cfg)
    if error:
        return page(f"Connect {source.title()}",
                    f'<div class="answer">Connection denied: {esc(error)}</div>')
    if not sources.verify_oauth_state(state, source, tenant_id, cfg.session_secret):
        raise HTTPException(status_code=400, detail="OAuth state is invalid for this account")
    try:
        adapter.handle_callback(cfg, con, code, state)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse("/ui/refresh", status_code=303)


@ui_router.post("/ui/refresh")
def ui_refresh_start(request: Request, con=Depends(get_db)):
    cfg = Config.from_env()
    # Import gate: sync is allowed while the account is under its entitlement (free slice +
    # purchased import_limit; None = unlimited). At the cap, send them to the billing slider.
    cap = storage.effective_import_cap(con, cfg.free_bookmark_limit)
    if cap is not None:
        if storage.post_count(con, "x") >= cap:
            return RedirectResponse(url="/ui/billing", status_code=303)
    jobs.start(resolve_tenant(request, cfg))  # sync runs under THIS user's tenant
    return RedirectResponse(url="/ui/refresh", status_code=303)


def _reconnect_link(prominent: bool = False) -> str:
    """Re-run the X authorization to replace a dead/rotated token. Points at the existing
    connect flow (/oauth/login), which saves fresh tokens to the CURRENT session tenant and
    (re)links the X identity — safe for accounts whose x_user_id was never linked."""
    if prominent:
        return ('<p><a class="stat" style="display:inline-block;text-decoration:none;'
                'border-color:var(--accent)" href="/oauth/login">'
                '<b style="font-size:1rem">↻ Reconnect X</b>'
                '<span>re-authorize to sync — your bookmarks stay put</span></a></p>')
    return ('<p class=muted style="margin-top:.7rem;font-size:.82rem">'
            'Sync failing or X disconnected? '
            '<a href="/oauth/login">Reconnect your X account</a>.</p>')


def _done_ctas() -> str:
    """The established done-state CTA composition, shared by fallback and pending states."""
    return (
        '<p><a class="stat" style="display:inline-block;text-decoration:none;margin-right:.6rem" '
        'href="/ui/ask"><b style="font-size:1rem">Ask your first question →</b>'
        '<span>“what did I save about…?”</span></a>'
        '<a class="stat" style="display:inline-block;text-decoration:none" '
        'href="/ui/feed"><b style="font-size:1rem">Browse your feed →</b>'
        '<span>color-coded by topic</span></a></p>'
    )


def _thread_state_json(turns: list, groups: list) -> str:
    """Serialize client-held Ask state safely for embedding in an inline script."""
    return json.dumps({"h": turns, "s": groups}, separators=(",", ":")).replace("</", "<\\/")


def _auto_answer_fragment(con, state: dict, *, capped: bool) -> str:
    """Render persisted answer/citations; Continue seeds bounded Ask state only when clicked."""
    citations = [str(i) for i in state["citations"]]
    cited_posts = posts_by_ids(con, citations)
    cited = set(citations)
    refno = {post_id: i + 1 for i, post_id in enumerate(citations)}
    cards = "".join(_cited_card(post, cited, refno, state["answer"])
                    for post in cited_posts)
    sources = trim_sources([{
        "q": state["q"],
        "ids": state["retrieved_ids"],
        "cited": citations,
    }])
    turns = trim_history([
        {"role": "user", "content": state["q"]},
        {"role": "assistant", "content": state["answer"]},
    ])
    payload = _thread_state_json(turns, sources)
    continue_js = (
        "try{localStorage.setItem('xbb_thread',JSON.stringify(" + payload + "));"
        "navigator.sendBeacon('/ui/t?e=auto_answer_continue','')}catch(e){}"
    )
    source_html = f'<h3>From your library</h3><div class="cards">{cards}</div>' if cards else ""
    continue_cta = "" if capped else (
        '<a class="btnlink" href="/ui/ask" data-component-id="continue-conversation-cta" '
        f'onclick="{esc(continue_js)}">Continue this conversation →</a>'
    )
    return (
        '<div data-component-id="auto-answer-fragment">'
        '<div class="answer" style="background:var(--accent-soft);color:var(--accent-ink);'
        'border-radius:12px;padding:.7rem 1rem;margin:1.6rem 0 .5rem;font-weight:600">'
        f'{esc(state["q"])}</div>'
        f'<div class="answer">{md_lite(state["answer"])}</div>'
        f'{source_html}<p class="row" style="margin-top:1rem">{continue_cta}'
        '<a class="btnlink ghost" href="/ui/feed">Browse your feed →</a></p>'
        '</div>'
    )


@ui_router.post("/ui/refresh/{source}")
def ui_refresh_source(source: str, request: Request):
    try:
        sources.get_adapter(source)
    except sources.SourceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    jobs.start_source(resolve_tenant(request, Config.from_env()), source)
    return RedirectResponse(url="/ui/refresh", status_code=303)


@ui_router.get("/ui/refresh")
def ui_refresh(request: Request, con=Depends(get_db)):
    cfg = Config.from_env()
    tenant = resolve_tenant(request, cfg)
    s = jobs.status(tenant)
    running = s["running"]
    # First-run: adapt to HOW they signed up. X sign-in already granted bookmark access
    # (one tap = connected); magic-link users still need the Connect X step first.
    first_run = (not running and s["step"] in ("idle",)
                 and storage.post_count(con, "x") == 0)  # browser-only users still need Connect X
    btn_label = "Syncing…" if running else "↻ Sync now"
    if s["error"] == "x_connection_expired":
        # Dead X token — X periodically requires re-authorizing. Lead with the reconnect,
        # no failing Sync button (it'd just error again).
        return page("Sync",
                    '<div class="answer" style="border-left-color:#d64545">⚠️ Your X '
                    "connection expired — X occasionally requires re-authorizing. Reconnect "
                    "below and your sync will work again. Your bookmarks, categories, and "
                    "credits all stay exactly as they are.</div>"
                    + _reconnect_link(prominent=True))
    if s["error"] == "x_api_credits":
        # Platform-wide X API credit exhaustion (402) — our bill, not their account. Honest
        # and calm; the Sync button stays because a retry works the moment credits are back.
        state = ('<div class="answer" style="border-left-color:#d64545">⚠️ X is briefly '
                 "limiting our bookmark fetching — nothing is wrong with your account or "
                 "payment. Please try again in a little while; your bookmarks, categories, "
                 "and credits are all safe.</div>")
    elif s["error"]:
        state = f'<div class="answer" style="border-left-color:#d64545">⚠️ {esc(s["error"])}</div>'
    elif s["step"] == "done":
        # The conversion moment: their library just got organized — hand them the next step.
        # Cap-hit free users get honest partial-library framing (value first, price later —
        # the first-answer card and banner carry the actual upsell).
        capped = storage.is_capped_free(con, cfg.free_bookmark_limit)
        if capped:
            logger.info("funnel.upsell_viewed surface=post_sync tenant=%s", tenant)
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
        auto_state = autoanswer.load(con) if cfg.auto_answer_enabled_for(tenant) else None
        if auto_state and auto_state.get("status") == "ready":
            if storage.claim_state(con, autoanswer.SHOWN_KEY, "1"):
                logger.info("funnel.auto_answer_shown tenant=%s", tenant)
            state = lead + _auto_answer_fragment(con, auto_state, capped=capped)
        elif autoanswer.is_pending_fresh(auto_state):
            state = (
                lead
                + '<div class="answer" data-component-id="auto-answer-pending">'
                '<span class="thinking"><span class="spinner"></span> '
                'Composing your first answer…</span><br>'
                '<span class=muted>This page refreshes automatically…</span></div>'
                + _done_ctas()
                + '<script>setTimeout(function(){location.reload()},3000)</script>'
            )
        else:
            state = lead + _done_ctas()
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
        return page("Sync", state + _source_controls(con, cfg))  # no X sync button until connected
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
        "reaches bookmarks already synced, so it only fetches what's new. For X only, "
        "bookmarks beyond your free slice use one import each (1¢). Browser, Reddit, and "
        "GitHub remain unlimited and free.</p>"
    )
    # Always-available escape hatch for a dead token (the app can't always detect one before
    # you press Sync): a quiet reconnect link for anyone who's connected.
    if xapi.is_connected(con):
        note += _reconnect_link()
    return page("Sync", state + form + _source_controls(con, cfg) + note)


# --------------------------------------------------------------------- browser import

MAX_IMPORT_UPLOAD_BYTES = 10 * 1024 * 1024  # a 20k-bookmark export is ~5 MB; 10 caps abuse


def _import_body(con, cfg: Config, error: str | None = None, notice: str | None = None) -> str:
    used = storage.post_count(con, "browser")
    msg = ""
    if error:
        msg = f'<div class="answer" style="border-left-color:#d64545">⚠️ {esc(error)}</div>'
    elif notice:
        msg = f'<div class="answer">{esc(notice)}</div>'
    return (
        "<p class=lead>Bring the rest of your saved web: upload your browser's bookmark "
        "export and it gets embedded, labeled and searchable alongside your X bookmarks. "
        "Browser bookmark imports are <b>unlimited and free</b> and never use your X imports.</p>"
        + msg +
        '<h3>1 · Export from your browser</h3>'
        "<p><b>Chrome</b> — <span class=muted>⋮ menu → Bookmarks and lists → Bookmark "
        "manager → ⋮ (top right) → <b>Export bookmarks</b></span><br>"
        "<b>Firefox</b> — <span class=muted>Ctrl/⌘+Shift+O → Import and Backup → "
        "<b>Export Bookmarks to HTML…</b></span><br>"
        "<span class=muted>Safari and Edge exports (same HTML format) work too.</span></p>"
        "<h3>2 · Upload the file</h3>"
        '<form method=post action="/ui/import" enctype="multipart/form-data" class=row>'
        '<input type=file name=file accept=".html,.htm,text/html" required>'
        "<button>Import bookmarks</button></form>"
        f"<p class=muted style='margin-top:1.2rem'>{used:,} browser bookmarks imported · "
        "re-uploading is safe (already-imported links are skipped) · "
        "folders are kept as context for the AI labeling.</p>"
    )


@ui_router.get("/ui/import")
def ui_import(con=Depends(get_db)):
    return page("Import", _import_body(con, Config.from_env()))


@ui_router.post("/ui/import")
def ui_import_post(request: Request, file: UploadFile = File(...), con=Depends(get_db)):
    cfg = Config.from_env()
    raw = file.file.read(MAX_IMPORT_UPLOAD_BYTES + 1)
    if len(raw) > MAX_IMPORT_UPLOAD_BYTES:
        return page("Import", _import_body(con, cfg, error="That file is over 10 MB — upload "
                    "a bookmarks export, not a page archive."))
    content = raw.decode("utf-8", errors="replace")
    low = content.lower()
    if "netscape-bookmark" not in low and "<dl" not in low:
        return page("Import", _import_body(con, cfg, error="That doesn't look like a bookmarks "
                    "export — use your browser's “Export bookmarks” (HTML) file."))

    parsed = bookmarks.parse_netscape_html(content)
    if not parsed:
        return page("Import", _import_body(con, cfg, error="No importable links found in that "
                    "file (bookmarklets and browser smart-folders are skipped)."))

    existing = {r[0] for r in
                con.execute("SELECT id FROM posts WHERE source = 'browser'").fetchall()}
    fresh = [bm for bm in parsed if bookmarks.record_id(bm["url"]) not in existing]
    dup = len(parsed) - len(fresh)
    if not fresh:
        return page("Import", _import_body(con, cfg, notice=f"All {len(parsed):,} bookmarks in "
                    "that file are already in your library — nothing new to import."))
    # Browser is unlimited/free. Sorting still gives deterministic newest-first rank assignment.
    fresh.sort(key=lambda b: b.get("add_date") or 0, reverse=True)
    kept = fresh

    base = con.execute("SELECT COALESCE(MAX(bm_rank), 0) FROM posts").fetchone()[0]
    for rec in bookmarks.to_records(kept, base):
        ingestion._upsert_post(con, rec)
    con.commit()
    tenant = resolve_tenant(request, cfg)
    logger.info("import.web tenant=%s imported=%d dup=%d", tenant, len(kept), dup)
    jobs.start_enrich(tenant, len(kept))  # embed + label in the background
    return RedirectResponse(url="/ui/refresh", status_code=303)  # existing progress page


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
        "Complete your library — 1¢ per import, from $3.<br>"
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
        '<a href="/ui/ask" style="font-size:.85rem;font-weight:600;text-decoration:none;'
        "color:var(--accent-ink);padding:.3rem .7rem;border:1px solid var(--line-2);"
        'border-radius:8px" '
        "onclick=\"try{localStorage.removeItem('xbb_thread')}catch(e){}\">"
        "↺ New chat</a>"
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


# Restore the most recent conversation from localStorage when the user navigates back to Ask
# (owner request: switch to Feed and back, the thread is still there). The stored state is
# auto-POSTed to /ui/ask/restore so the SERVER renders the full chat layout — thread bubbles
# AND the sources pane (side tweets need server-rendered cards; a client-side rebuild dropped
# them — owner bug report 2026-07-13).
_RESTORE_JS = (
    "<script>(function(){try{"
    "var d=JSON.parse(localStorage.getItem('xbb_thread')||'null');"
    "if(!d||!d.h||!d.h.length)return;"
    "var f=document.createElement('form');f.method='post';f.action='/ui/ask/restore';"
    "function inp(n,v){var i=document.createElement('input');i.type='hidden';i.name=n;"
    "i.value=JSON.stringify(v);f.appendChild(i);}"
    "inp('history',d.h);if(d.s&&d.s.length)inp('sources',d.s);"
    "document.body.appendChild(f);f.submit();"
    "}catch(e){}})();</script>"
)

def _new_chat_btn() -> str:
    """Prominent New-chat control at the top of a conversation view (owner: the composer's
    link alone was too easy to miss). Clears the stored thread and starts fresh."""
    return ('<div class="view-toggle" style="margin-bottom:.5rem">'
            '<a href="/ui/ask" class="on" '
            "onclick=\"try{localStorage.removeItem('xbb_thread')}catch(e){}\">"
            "↺ New chat</a></div>")


# Snap the thread pane so the newest question sits at the top with its answer below (shared
# by the live-answer render and the restored-conversation render).
_SNAP_JS = (
    "<script>var _ac=document.querySelector('.ask-cols');"
    "if(_ac)window.scrollTo(0,_ac.getBoundingClientRect().top+window.pageYOffset-10);"
    "var _aq=document.getElementById('latestq'),"
    "_as=document.getElementById('askscroll');"
    "if(_aq&&_as){var _lc=_as.lastElementChild,"
    "_bot=_lc.offsetTop+_lc.offsetHeight+16,"
    "_pad=_aq.offsetTop-6+_as.clientHeight-_bot;"
    "if(_pad>0)_as.style.paddingBottom=_pad+'px';"
    "_as.scrollTop=Math.max(0,_aq.offsetTop-6);}</script>"
)


def _save_thread_js(turns: list, groups: list) -> str:
    """Persist the finished exchange client-side so navigating away and back restores it."""
    payload = _thread_state_json(turns, groups)
    return (f"<script>try{{localStorage.setItem('xbb_thread',"
            f"JSON.stringify({payload}))}}catch(e){{}}</script>")


# Client-side funnel beacons (sendBeacon → 204). Allowlisted names only; tenant + event,
# never content — same telemetry rules as ui.view.
_TRACK_EVENTS = {
    "composer_focused": "funnel.feed_composer_focused",
    "auto_answer_continue": "funnel.auto_answer_continued",
}
_TRACK_SOURCES = {"feed"}  # allowlisted `src` attribution values for ask entry points


@ui_router.post("/ui/t")
def ui_track(request: Request, e: str = ""):
    event = _TRACK_EVENTS.get(e)
    if event:
        logger.info("%s tenant=%s", event, resolve_tenant(request, Config.from_env()))
    return Response(status_code=204)


@ui_router.get("/ui/ask")
def ui_ask(question: str = "", src: str = "", con=Depends(get_db), ai=Depends(get_ai)):
    banner = _capped_banner(con, Config.from_env(), "banner_ask")
    # A prefilled question (?question=...) means a fresh intent — skip the restore.
    restore = _RESTORE_JS if not question else ""
    auto = ""
    if question:
        # Arriving with a question (the feed composer) IS the ask — start immediately.
        # replaceState first so refresh/back lands on a clean /ui/ask instead of a URL
        # that would re-submit (and re-charge) the same question.
        src = src if src in _TRACK_SOURCES else ""
        add_src = (
            "var s=document.createElement('input');s.type='hidden';s.name='src';"
            f"s.value={json.dumps(src)};f.appendChild(s);" if src else ""
        )
        auto = ("<script>(function(){var f=document.getElementById('askform');if(!f)return;"
                + add_src +
                "try{history.replaceState({},'','/ui/ask')}catch(e){}"
                "f.requestSubmit();})()</script>")
    return page("Ask", banner + _ask_form(question) + restore + auto)


@ui_router.post("/ui/ask")
def ui_ask_post(request: Request, question: str = Form(...), history: str = Form(""),
                sources: str = Form(""), src: str = Form(""),
                con=Depends(get_db), ai=Depends(get_ai)):
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
    tenant = resolve_tenant(request, cfg)
    src = src if src in _TRACK_SOURCES else ""
    if src:
        logger.info("funnel.feed_composer_submitted tenant=%s", tenant)
    # Owner perk: deeper retrieval against the 17k corpus.
    k = 50 if (cfg.owner_tenant_id and tenant == cfg.owner_tenant_id) else 30
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
    if src:  # completed answer for a feed-composer ask (started == submitted: same request)
        logger.info("funnel.feed_composer_answered tenant=%s", tenant)
    cited = set(result["citations"])
    retrieved = result["retrieved"]
    # Raw post ids leaking into the prose read as garbage numbers (owner report). The prompt
    # now forbids them, but models regress: swap any retrieved id in the text for a numbered
    # [n] marker and number the matching card's badge so the reference points somewhere.
    ans_text, refno = autoanswer.rewrite_citation_ids(
        result.get("answer"),
        [str(post.get("id") or "") for post in retrieved],
        [str(citation) for citation in result["citations"]],
    )
    # Next form carries the thread + accumulated sources including this exchange.
    new_turns = turns + [{"role": "user", "content": question},
                         {"role": "assistant", "content": ans_text}]
    groups = merge_sources(prior_sources, question,
                           [str(p["id"]) for p in retrieved], result["citations"])
    kept_turns = trim_history(new_turns)
    form = _ask_form("", autofocus=False, history=kept_turns, sources=groups)
    save_js = _save_thread_js(kept_turns, groups)

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
            _new_chat_btn()
            + '<div class="ask-cols">'
            '<div class="ask-left">'
            f'<div class="ask-scroll" id="askscroll">{thread}{latest_q}{answer}</div>'
            f'<div class="ask-composer">{form}</div>'
            "</div>"
            f'<div class="ask-right">{"".join(right)}</div>'
            "</div>"
            + _SNAP_JS
        )
        return page("Ask", body + save_js, wide=True, rail=True)
    return page("Ask", _new_chat_btn() + thread + latest_q + answer + form + save_js)


@ui_router.post("/ui/ask/restore")
def ui_ask_restore(request: Request, history: str = Form(""), sources: str = Form(""),
                   con=Depends(get_db)):
    """Re-render a locally-stored conversation server-side — full chat layout including the
    sources pane (cards must be server-rendered from ids). No AI call, no billing: this only
    redraws what the user already paid for. Same trust model as the ask fields: everything
    is validated and bounded by trim_history/trim_sources."""
    try:
        turns = trim_history(json.loads(history)) if history else []
    except (json.JSONDecodeError, TypeError):
        turns = []
    try:
        groups = trim_sources(json.loads(sources)) if sources else []
    except (json.JSONDecodeError, TypeError):
        groups = []
    if not turns:
        return RedirectResponse("/ui/ask?question=+", status_code=303)  # nothing to restore
    form = _ask_form("", autofocus=False, history=turns, sources=groups or None)
    # Last exchange gets the anchor treatment; earlier turns render as the compact thread.
    if len(turns) >= 2 and turns[-2]["role"] == "user" and turns[-1]["role"] == "assistant":
        thread = _thread_html(turns[:-2])
        latest_q = _question_card(turns[-2]["content"], anchor=True)
        answer = f'<div class="answer">{md_lite(turns[-1]["content"])}</div>'
    else:
        thread, latest_q, answer = _thread_html(turns), "", ""
    all_posts = {p["id"]: p for p in
                 posts_by_ids(con, [i for g in groups for i in g["ids"]])}
    right = []
    for g in groups:
        posts = [all_posts[i] for i in g["ids"] if i in all_posts]
        if not posts:
            continue
        grefno = {pid: n + 1 for n, pid in enumerate(g["cited"])}
        gcards = "".join(_cited_card(p, set(g["cited"]), grefno) for p in posts)
        right.append(f'<h3 class="src-group">from: “{esc(g["q"][:120])}”</h3>'
                     f'<div class="cards">{gcards}</div>')
    if right:
        body = (
            _new_chat_btn()
            + '<div class="ask-cols">'
            '<div class="ask-left">'
            f'<div class="ask-scroll" id="askscroll">{thread}{latest_q}{answer}</div>'
            f'<div class="ask-composer">{form}</div>'
            "</div>"
            f'<div class="ask-right">{"".join(right)}</div>'
            "</div>" + _SNAP_JS
        )
        return page("Ask", body, wide=True, rail=True)
    return page("Ask", _new_chat_btn() + thread + latest_q + answer + form)


@ui_router.get("/ui/feedback")
def ui_feedback():
    body = (
        "<p class=lead>Found a bug? Want a feature? Tell us — this goes straight to a human.</p>"
        '<form method=post action="/ui/feedback">'
        '<textarea name="message" required maxlength="4000" rows="6" placeholder="What\'s on your mind?" '
        'style="width:100%;max-width:640px;padding:.8rem 1rem;font-size:1rem;font-family:inherit;'
        'border:1px solid var(--line-2);border-radius:12px;background:var(--panel);box-shadow:var(--shadow)">'
        "</textarea>"
        '<div style="margin-top:.6rem;max-width:640px">'
        '<input type=email name=email maxlength="254" placeholder="you@example.com (optional)" '
        'style="width:100%;padding:.6rem .9rem;font-size:.95rem;font-family:inherit;'
        'border:1px solid var(--line-2);border-radius:10px;background:var(--panel)">'
        '<p class=muted style="font-size:.82rem;margin:.35rem 0 0">Want a reply? Leave your '
        "email — or we'll DM your X account (make sure your DMs are open).</p></div>"
        '<div class=row style="margin-top:.6rem"><button>Send feedback</button></div></form>'
    )
    return page("Feedback", body)


@ui_router.post("/ui/feedback")
def ui_feedback_post(request: Request, message: str = Form(...), email: str = Form(""),
                     con=Depends(get_db)):
    from . import mail
    cfg = Config.from_env()
    tenant = resolve_tenant(request, cfg)
    row = con.execute("SELECT email, x_handle FROM accounts WHERE id = %s::uuid",
                      (tenant,)).fetchone()
    acct_email, handle = (row or (None, None))
    given = email.strip()[:254]
    given = given if ("@" in given and "." in given.rsplit("@", 1)[-1]) else ""
    if given and not acct_email:
        # Volunteered reply address doubles as the account email (never overwrites).
        if storage.set_account_email(con, tenant, given):
            logger.info("billing.email_captured tenant=%s src=feedback", tenant)
    # Reply channel, best first: volunteered email > account email > X DM (form warns the
    # user their DMs must be open) > bare tenant id (email signups always have an email).
    sender = (f"{given} (volunteered)" if given
              else acct_email
              or (f"@{handle} — reply via X DM" if handle else tenant))
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


@ui_router.get("/ui/graph")
def ui_graph(con=Depends(get_db)):
    tree = categorize.category_tree(con)
    groups = [(group["parent"], group["total"]) for group in tree if group["total"]]
    total = storage.post_count(con)
    fallback = (
        '<div><b>Your library is ready to map.</b><br>'
        f'{total:,} bookmarks will appear around their theme communities when the '
        'interactive graph loads.<noscript><br>JavaScript is required for the interactive '
        'view; your library data remains available in Feed and Categories.</noscript></div>'
    )
    body = (
        '<p class="lead">Your bookmarks form one connected network rooted on you, with radial '
        'theme communities and semantic bridges between ideas.</p>'
        + legend(groups, graph_mode=True)
        + graph_visualization(fallback)
    )
    return page("Your knowledge graph", body, wide=True)


_PAGE = 150


def _card_view(request: Request, view: str, base: str, qs: str = ""):
    """Shared grid/list card-view machinery for any page of post cards: resolve the view
    (explicit ?view= wins and is remembered; else the xbb_feed_view cookie — ONE preference
    app-wide), build the toggle HTML, and hand back a page-wrapper that persists the choice.
    Returns (toggle_html, cards_class, respond)."""
    if view not in ("grid", "list"):
        view = request.cookies.get("xbb_feed_view", "grid")
        view = view if view in ("grid", "list") else "grid"
    toggle = (
        '<div class="view-toggle">'
        f'<a href="{base}?view=grid{qs}" class="{"on" if view == "grid" else ""}">▦ Grid</a>'
        f'<a href="{base}?view=list{qs}" class="{"on" if view == "list" else ""}">☰ List</a>'
        "</div>"
    )
    cards_class = "cards list" if view == "list" else "cards"

    def respond(title: str, body: str):
        resp = page(title, body)
        resp.set_cookie("xbb_feed_view", view, max_age=365 * 24 * 3600, samesite="lax")
        return resp

    return toggle, cards_class, respond


# Feed source chips: friendly labels for known adapters; unknown future sources fall back
# to a capitalized name automatically, so new adapters appear with zero UI work.
def _source_chips(
    con, active_source: str, query: dict[str, str]
) -> tuple[str, str]:
    """Right-aligned source filter (All / 𝕏 / 🌐 / …) mirroring the category legend: chips
    are data-driven from the sources actually present, with counts. Hidden while the library
    is single-source. Returns (chips_html, validated_source)."""
    rows = con.execute(
        "SELECT source, COUNT(*) FROM posts GROUP BY source ORDER BY 2 DESC").fetchall()
    known = {r[0] for r in rows}
    source = active_source if active_source in known else ""
    if len(rows) < 2:
        return "", source
    all_query = urlencode(query)
    all_href = "/ui/feed" + (f"?{all_query}" if all_query else "")
    chips = [f'<a href="{esc(all_href)}" class="{"on" if not source else ""}">All</a>']
    for s, n in rows:
        label = _SOURCE_LABELS.get(s, s.capitalize())
        href = "/ui/feed?" + urlencode({"source": s, **query})
        chips.append(f'<a href="{esc(href)}" '
                     f'class="{"on" if source == s else ""}">{label} '
                     f'<span class=muted>{n:,}</span></a>')
    return ('<div class="view-toggle" style="margin-right:.8rem">' + "".join(chips) + "</div>",
            source)


@ui_router.get("/ui/feed")
def ui_feed(request: Request, parent: str = "", offset: int = 0, partial: int = 0,
            view: str = "", source: str = "", con=Depends(get_db)):
    active = parent or None
    filter_query = ({"parent": active} if active else {})
    chip_query = {**filter_query, **({"view": view} if view else {})}
    chips, src = _source_chips(con, source, chip_query)
    posts = categorize.feed_posts(con, parent=active, limit=_PAGE, offset=offset,
                                  source=src or None)

    # Partial: just the card HTML, for the infinite-scroll appender to insert.
    if partial:
        return HTMLResponse("".join(post_card(p) for p in posts))

    view_query = {**filter_query, **({"source": src} if src else {})}
    encoded_view_query = urlencode(view_query)
    qs_view = f"&{encoded_view_query}" if encoded_view_query else ""
    toggle, cards_class, respond = _card_view(request, view, "/ui/feed", qs_view)

    cfg = Config.from_env()
    tree = categorize.category_tree(con)
    groups = [(g["parent"], g["total"]) for g in tree]
    where = f" in {esc(active)}" if active else ""
    note = f"{chips}{toggle}<p class=lead>Newest first{where} · scroll to keep loading. Tap a color to filter.</p>"
    banner = _capped_banner(con, cfg, "banner_feed")
    # The feed is where questions occur to people (26 feed-first vs 8 ask-first post-sync,
    # 2026-07-22): capture the question right here. GET → /ui/ask?question=…&src=feed, which
    # auto-starts the answer on arrival. Example rotates through THEIR categories, not a
    # generic "ask anything". Funnel: viewed → focused (beacon) → submitted → answered.
    examples = [c["name"] for g in tree for c in g["children"]]
    example = random.choice(examples) if examples else "AI"
    composer = (
        '<form class="feed-ask" method="get" action="/ui/ask">'
        '<input type="hidden" name="src" value="feed">'
        '<input type="text" name="question" required '
        f'placeholder="Ask your bookmarks anything… what did I save about {esc(example)}?">'
        '<button class="ghost">Ask →</button></form>'
        "<script>(function(){var i=document.querySelector('.feed-ask input[name=question]');"
        "if(i)i.addEventListener('focus',function(){"
        "try{navigator.sendBeacon('/ui/t?e=composer_focused')}catch(e){}},{once:true});})()"
        "</script>"
    )
    logger.info("funnel.feed_composer_viewed tenant=%s", resolve_tenant(request, cfg))

    def _respond(body: str):
        return respond("Feed", body)

    if not posts:
        return _respond(banner + chips + legend(groups, active) + toggle
                        + "<p class=muted>Nothing here yet.</p>")

    feed = f'<div id="feed" class="{cards_class}">{"".join(post_card(p) for p in posts)}</div>'
    sentinel = '<div id="more" class="muted" style="text-align:center;padding:1.6rem">loading…</div>'
    done = "true" if len(posts) < _PAGE else "false"
    js = (
        "<script>(function(){var off=" + str(_PAGE) + ",busy=false,done=" + done
        + ",parent=" + json.dumps(active or "")
        + ",src=" + json.dumps(src or "")
        + ",feed=document.getElementById('feed'),more=document.getElementById('more');"
        "if(done){more.remove();return;}"
        "var io=new IntersectionObserver(function(es){"
        "if(!es[0].isIntersecting||busy||done)return;busy=true;"
        "var u='/ui/feed?partial=1&offset='+off+(parent?'&parent='+encodeURIComponent(parent):'')"
        "+(src?'&source='+encodeURIComponent(src):'');"
        "fetch(u).then(function(r){return r.text();}).then(function(h){"
        "var n=(h.match(/class=\"post\"/g)||[]).length;"
        "if(n===0){done=true;more.remove();return;}"
        "if(window.__masonryAdd){window.__masonryAdd(feed,h);}else{feed.insertAdjacentHTML('beforeend',h);}"
        "off+=" + str(_PAGE) + ";busy=false;"
        "if(n<" + str(_PAGE) + "){done=true;more.remove();}"
        "});});io.observe(more);})();</script>"
    )
    return _respond(banner + composer + legend(groups, active) + note + feed + sentinel + js)


@ui_router.get("/ui/categories/{category_id}")
def ui_category(request: Request, category_id: int, view: str = "", con=Depends(get_db)):
    row = con.execute("SELECT name FROM categories WHERE id = %s", (category_id,)).fetchone()
    name = row[0] if row else "Category"
    posts = categorize.posts_in_category(con, category_id)
    toggle, cards_class, respond = _card_view(request, view, f"/ui/categories/{category_id}")
    head = (
        f'{toggle}<p><a href="/ui/categories">← all categories</a></p>'
        f"<p class=lead>{len(posts):,} bookmarks in this category.</p>"
    )
    cards = (
        f'<div class="{cards_class}">{"".join(post_card(p) for p in posts)}</div>'
        if posts
        else "<p class=muted>No posts.</p>"
    )
    return respond(name, head + cards)


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
