"""Local web app: search, ask, browse-by-category, and taxonomy review.

This module wires routes to the tested logic via dependency injection (`get_db`, `get_ai`)
so the endpoints are testable with a fake AI client. HTML screens are layered on next; for
now the routes return JSON.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import Depends, FastAPI, Form, Request, Response
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import auth, authui, billing, categorize, credits, jobs, legal, mail, pricing, storage
from .config import Config
from .deps import SESSION_COOKIE, get_ai, get_db, resolve_tenant
from .log import logger
from .search import index_posts, search
from .storage import connect
from .templates import esc, page
from .webui import ui_router


def _apply_billing_event(con, info: dict) -> None:
    """Translate a summarized Stripe event into an account plan update."""
    if info["type"] == "checkout.session.completed":
        account_id = info.get("client_reference_id")
        if account_id:
            storage.set_subscription(
                con, account_id, plan="pro", subscription_status="active",
                stripe_customer_id=info.get("customer_id"),
                stripe_subscription_id=info.get("subscription_id"),
                monthly_quota_usd=None,  # Pro = uncapped
            )
        return
    # customer.subscription.updated / .deleted — find the account by its Stripe customer id
    account_id = storage.account_by_stripe_customer(con, info.get("customer_id"))
    if not account_id:
        return
    active = info["type"] == "customer.subscription.updated" and info.get("status") == "active"
    storage.set_subscription(
        con, account_id, plan=("pro" if active else "free"),
        subscription_status=(info.get("status") or "canceled"), monthly_quota_usd=None,
    )


def _apply_payment_event(con, event: dict) -> bool:
    """Handle a one-time payment (import entitlement, credit top-up, legacy ingestion).
    Returns True if it was a one-time payment we handled (caller skips the subscription path)."""
    if event.get("type") == "checkout.session.expired":
        # Funnel telemetry only (fires ~24h after the session was created and abandoned).
        obj = event.get("data", {}).get("object", {})
        logger.info("funnel.checkout_abandoned kind=%s tenant=%s",
                    (obj.get("metadata") or {}).get("kind"), obj.get("client_reference_id"))
        return True
    if event.get("type") != "checkout.session.completed":
        return False
    obj = event.get("data", {}).get("object", {})
    if obj.get("mode") != "payment":
        return False  # subscription checkout — not ours
    account_id = obj.get("client_reference_id")
    meta = obj.get("metadata") or {}
    kind = meta.get("kind")
    paid = (obj.get("amount_total") or 0) / 100.0
    logger.info("funnel.checkout_completed kind=%s amount_usd=%.2f tenant=%s",
                kind, paid, account_id)
    buyer_email = obj.get("customer_email") or (obj.get("customer_details") or {}).get("email")
    buyer = buyer_email or account_id
    # Opportunistic email capture: X-sign-in accounts have no email until Stripe collects one.
    if account_id and buyer_email and not storage.get_account_email(con, account_id):
        if storage.set_account_email(con, account_id, buyer_email):
            logger.info("billing.email_captured tenant=%s", account_id)
        else:
            logger.info("billing.email_capture_conflict tenant=%s", account_id)
    if account_id and kind == "import":
        try:
            count = int(meta.get("count", 0))
        except (TypeError, ValueError):
            count = 0
        if count > 0:
            storage.add_import_limit(con, account_id, count)
            # Payment ref kept for support context (on-request refunds; no auto true-up).
            storage.set_import_payment(con, account_id, obj.get("payment_intent"), paid)
            logger.info("billing.import_paid tenant=%s count=%d paid=%.2f", account_id, count, paid)
            _purchase_alert(f"{buyer} bought an import of up to {count:,} bookmarks (${paid:.2f})")
            # Fulfillment starts NOW, not when the customer finds the Sync button (first
            # buyer waited 43 minutes, nearly paid twice, and emailed support). Best-effort:
            # a job hiccup must never 500 the webhook (Stripe would retry the payment event).
            try:
                jobs.start(account_id)
                logger.info("sync.autostart reason=import_paid tenant=%s", account_id)
            except Exception:
                logger.exception("sync.autostart_failed tenant=%s", account_id)
    elif account_id and kind == "ingestion":  # legacy fixed-price full unlock
        storage.set_ingestion_paid(con, account_id, True)
        _purchase_alert(f"{buyer} bought the full-import unlock (${paid:.2f})")
    elif account_id and kind == "credits":
        if paid > 0:
            try:  # pack bonus computed at checkout, carried in metadata; fall back to $ paid
                grant = float(meta.get("grant") or paid)
            except (TypeError, ValueError):
                grant = paid
            storage.add_credits(con, account_id, max(grant, paid))
            logger.info("billing.credits_paid tenant=%s paid=%.2f grant=%.2f", account_id, paid, grant)
            _purchase_alert(f"{buyer} paid ${paid:.2f} for ${max(grant, paid):.2f} of credits")
    return True


def _purchase_alert(body: str) -> None:
    cfg = Config.from_env()
    mail.send_owner_alert("💰 x-bookmarks purchase", body, ses_sender=cfg.ses_sender,
                          owner_email=cfg.owner_alert_email, region=cfg.aws_region)


def _apply_invoice_event(con, event: dict) -> bool:
    """Monthly credit-subscription grant: each paid invoice for our credit sub adds the monthly
    credits. The subscription's metadata (set at checkout) is denormalized onto the invoice, so
    the event self-identifies — first invoice and every renewal behave identically."""
    if event.get("type") != "invoice.paid":
        return False
    obj = event.get("data", {}).get("object", {})
    meta = (obj.get("subscription_details") or {}).get("metadata") or {}
    account_id = meta.get("account_id")
    if meta.get("kind") == "credit_sub" and account_id:
        storage.add_credits(con, account_id, pricing.SUB_MONTHLY_CREDITS_USD)
        _purchase_alert(f"subscription invoice paid — account {account_id} granted "
                        f"${pricing.SUB_MONTHLY_CREDITS_USD:.2f} of monthly credits")
    return True


class Category(BaseModel):
    name: str
    definition: str | None = None


class TaxonomyIn(BaseModel):
    categories: list[Category]


class RenameIn(BaseModel):
    name: str


class AskIn(BaseModel):
    question: str
    k: int = 30
    # Prior conversation, client-held: [{role: user|assistant, content}, ...]. The server
    # stays stateless; ask.trim_history validates and bounds whatever arrives here.
    history: list[dict] = []


def create_app() -> FastAPI:
    app = FastAPI(title="x-bookmark-brain")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    # --- legal (public) ---
    @app.get("/terms")
    def terms_route():
        return legal.terms_page()

    @app.get("/privacy")
    def privacy_route():
        return legal.privacy_page()

    # --- SEO plumbing (public): the crawl map + crawler rules ---
    _BASE = "https://x-bookmarks.ai"

    @app.get("/sitemap.xml")
    def sitemap_route():
        urls = "".join(f"<url><loc>{_BASE}{p}</loc></url>"
                       for p in ("/", "/terms", "/privacy"))
        return Response(
            content='<?xml version="1.0" encoding="UTF-8"?>'
                    '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                    f"{urls}</urlset>",
            media_type="application/xml",
        )

    @app.get("/robots.txt")
    def robots_route():
        # Public marketing surface is crawlable; the signed-in app is not (it 303s to /login
        # anyway, but Disallow keeps crawlers from wasting the budget and logging noise).
        return Response(
            content=f"User-agent: *\nDisallow: /ui/\nDisallow: /billing/\n"
                    f"Sitemap: {_BASE}/sitemap.xml\n",
            media_type="text/plain",
        )

    # --- auth (magic-link sign in) ---
    @app.get("/login")
    def login_page_route():
        return authui.login_page()

    @app.post("/auth/request")
    def auth_request_route(request: Request, email: str = Form(...)):
        cfg = Config.from_env()
        token = auth.make_login_token(email, cfg.session_secret)
        link = str(request.base_url).rstrip("/") + "/auth/verify?token=" + token
        mail.send_login_link(email, link, ses_sender=cfg.ses_sender, region=cfg.aws_region)
        return authui.check_email_page(email)

    @app.get("/auth/verify")
    def auth_verify_route(token: str, con=Depends(get_db)):
        cfg = Config.from_env()
        email = auth.verify_login_token(token, cfg.session_secret)
        if not email:
            return authui.login_page(error="That sign-in link is invalid or expired.")
        is_new = con.execute("SELECT 1 FROM accounts WHERE email = %s", (email,)).fetchone() is None
        account_id = storage.get_or_create_account(con, email)
        logger.info("auth.email_signin tenant=%s created=%s", account_id, is_new)
        if is_new:
            mail.send_owner_alert("🆕 x-bookmarks signup", f"New account: {email}",
                                  ses_sender=cfg.ses_sender,
                                  owner_email=cfg.owner_alert_email, region=cfg.aws_region)
        session = auth.make_session_token(account_id, cfg.session_secret)
        resp = RedirectResponse("/", status_code=303)
        resp.set_cookie(SESSION_COOKIE, session, httponly=True, samesite="lax",
                        max_age=auth.SESSION_MAX_AGE_S)
        return resp

    @app.post("/auth/logout")
    def auth_logout_route():
        resp = RedirectResponse("/login", status_code=303)
        resp.delete_cookie(SESSION_COOKIE)
        return resp

    @app.get("/ui/account")
    def account_route(request: Request, con=Depends(get_db)):
        cfg = Config.from_env()
        token = request.cookies.get(SESSION_COOKIE)
        account_id = auth.verify_session_token(token, cfg.session_secret) if token else None
        if not account_id:
            return RedirectResponse("/login", status_code=303)
        email = storage.get_account_email(con, account_id) or "unknown"
        return authui.account_page(email)

    # --- billing (prepaid credits + one-time ingestion charge) ---
    def _current_account(request: Request, cfg: Config) -> str:
        token = request.cookies.get(SESSION_COOKIE)
        return (auth.verify_session_token(token, cfg.session_secret) if token else None) or cfg.tenant_id

    @app.get("/ui/billing")
    def billing_page_route(request: Request, src: str = "", con=Depends(get_db)):
        cfg = Config.from_env()
        bal = storage.credit_balance(con)
        cap = storage.effective_import_cap(con, cfg.free_bookmark_limit)
        free_left = max(cfg.free_asks_per_day - storage.free_asks_used_today(con), 0)
        asks = int(bal / cfg.ask_price_usd) if cfg.ask_price_usd else 0
        body = ""
        if src and cap is not None:
            # Arrived from an upsell prompt: explain exactly what "complete your library"
            # means before showing any controls (owner spec: no vague "upgrade" language).
            body += (
                '<div class="answer" style="border-left:4px solid var(--accent)">'
                "<b>Complete your library</b><ul style='margin:.5rem 0 0 1.1rem'>"
                f"<li>Your newest {cfg.free_bookmark_limit} bookmarks are already imported — "
                "this unlocks the rest.</li>"
                "<li>X doesn't reveal your exact total until the full library is scanned.</li>"
                "<li>You authorize the amount below before any large fetch begins.</li>"
                f"<li>{int(cfg.price_per_bookmark_usd * 100)}¢ per import, "
                f"${pricing.IMPORT_MIN_USD:.0f} minimum.</li>"
                "<li>Anything you don't use stays on your account as imports for everything "
                "you save next — refund on request anytime.</li></ul></div>"
            )
        body += (f'<div class="answer"><b>{free_left} free question(s) left today</b> '
                 f"(resets daily) · <b>credit balance: ${bal:.2f}</b> "
                 f"(~{asks} more at ${cfg.ask_price_usd:.2f} each).</div>")

        # --- import entitlement / slider ---
        if cap is None:
            body += "<p class=muted>✓ Full bookmark history unlocked.</p>"
        else:
            n = con.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
            body += (f"<p class=lead>{min(n, cap):,} of your {cap:,}-bookmark allowance imported "
                     f"({cfg.free_bookmark_limit:,} free"
                     + (f" + {cap - cfg.free_bookmark_limit:,} purchased" if cap > cfg.free_bookmark_limit else "")
                     + ").</p>")
            if cfg.stripe_secret_key:
                cents = int(cfg.price_per_bookmark_usd * 100)
                per = cfg.price_per_bookmark_usd
                default_usd = 10
                body += (
                    "<p><b>Buy imports</b> — each import brings one saved post into your "
                    f"library, {cents}¢ each, on top of your free {cfg.free_bookmark_limit}. "
                    "<b>Unused imports stay on your account</b> and cover whatever you save "
                    "next. Refunds on request, anytime.</p>"
                    '<form method=post action="/billing/checkout">'
                    '<input type=hidden name=kind value="import">'
                    f'<input type=range name=amount id=imp_usd min={pricing.IMPORT_MIN_USD:.0f} '
                    f'max={pricing.IMPORT_MAX_USD:.0f} step={pricing.IMPORT_STEP_USD:.0f} '
                    f'value="{default_usd}" style="width:100%" '
                    "oninput=\"document.getElementById('imp_amt').textContent=this.value;"
                    "document.getElementById('imp_n').textContent="
                    f"Math.round(this.value/{per}).toLocaleString()\">"
                    '<div class=row style="margin:.4rem 0 .6rem">'
                    f'<span>$<b id=imp_amt>{default_usd}</b> one-time</span>'
                    '<span style="margin-left:auto">= <b id=imp_n>'
                    f'{pricing.imports_for_usd(default_usd, per):,}</b> imports</span></div>'
                    "<button>Complete my library</button></form>"
                )

        # --- credits: custom one-time + subscription ---
        if cfg.stripe_secret_key:
            cents = int(round(cfg.ask_price_usd * 100))
            tiers = " · ".join(f"${floor:.0f}+ adds +{bonus:.0%}"
                               for floor, bonus in sorted(pricing.CREDIT_PACK_BONUS))
            # question count for the live readout: (amount * (1+bonus)) / price, bonus in JS
            js_bonus = "(v>=20?1.3:v>=10?1.2:v>=5?1.1:1)"
            body += (
                f'<div style="margin-top:1.4rem"><b>Buy questions</b> — {cents}¢ each, '
                f"bigger packs get bonus questions: {tiers}"
                '<form method=post action="/billing/checkout" class=row style="margin-top:.4rem">'
                '<input type=hidden name=kind value="credits">'
                f'<input type=number name=amount min={pricing.MIN_CREDIT_TOPUP_USD:.0f} '
                f'max={pricing.MAX_CREDIT_TOPUP_USD:.0f} step=1 value=10 '
                'style="width:6rem;padding:.5rem;border-radius:8px;border:1px solid var(--line-2)" '
                "oninput=\"var v=Number(this.value)||0;document.getElementById('topup_q').textContent="
                f"Math.floor(v*{js_bonus}/{cfg.ask_price_usd})\"> "
                f'<span class=muted>= <b id=topup_q>{int(pricing.credits_for_topup(10) / cfg.ask_price_usd) if cfg.ask_price_usd else 0}</b> questions</span> '
                "<button>Buy questions</button></form></div>"
            )
        else:
            body += "<p class=muted>Billing isn't configured.</p>"
        return page("Billing", body)

    @app.post("/billing/checkout")
    def checkout_route(request: Request, kind: str = Form("credits"),
                       count: int = Form(0), amount: float = Form(0.0), con=Depends(get_db)):
        cfg = Config.from_env()
        if not cfg.stripe_secret_key:
            return RedirectResponse("/ui/billing", status_code=303)
        account_id = _current_account(request, cfg)
        # None (X-sign-in account, no email yet) → Stripe collects the real one at checkout
        # and the webhook saves it back onto the account.
        email = storage.get_account_email(con, account_id)
        base = str(request.base_url).rstrip("/")
        common = dict(api_key=cfg.stripe_secret_key, customer_email=email,
                      client_reference_id=account_id,
                      success_url=base + "/billing/success", cancel_url=base + "/ui/billing")

        if kind == "import":
            price = max(pricing.IMPORT_MIN_USD, min(float(amount or 0), pricing.IMPORT_MAX_USD))
            n = pricing.imports_for_usd(price, cfg.price_per_bookmark_usd)
            if n <= 0:
                return RedirectResponse("/ui/billing", status_code=303)
            url = billing.create_amount_session(
                amount_usd=price,
                product_name=f"{n:,} imports — x-bookmarks.ai",
                metadata={"kind": "import", "count": str(n), "account_id": account_id}, **common)
        elif kind == "credits":
            # 2026-07-10 pivot: subscriptions are gone; packs grant bonus credits instead.
            amt = max(pricing.MIN_CREDIT_TOPUP_USD,
                      min(float(amount or 0), pricing.MAX_CREDIT_TOPUP_USD))
            grant = pricing.credits_for_topup(amt)
            q = int(grant / cfg.ask_price_usd) if cfg.ask_price_usd else 0
            url = billing.create_amount_session(
                amount_usd=amt, product_name=f"{q} questions (${grant:.2f} of credits) — x-bookmarks.ai",
                metadata={"kind": "credits", "grant": f"{grant:.2f}",
                          "account_id": account_id}, **common)
        else:  # legacy fixed-price full unlock
            if not cfg.stripe_ingest_price_id:
                return RedirectResponse("/ui/billing", status_code=303)
            url = billing.create_payment_session(
                price_id=cfg.stripe_ingest_price_id,
                metadata={"kind": "ingestion", "account_id": account_id}, **common)
        amt = price if kind == "import" else (amt if kind == "credits" else 0)
        logger.info("funnel.checkout_created kind=%s amount_usd=%.2f tenant=%s",
                    kind, amt, account_id)
        return RedirectResponse(url, status_code=303)

    @app.get("/billing/success")
    def billing_success_route():
        return page("Billing", '<div class="answer">🎉 Thanks! Your purchase is being applied. '
                    "If you bought imports, <b>your library is importing right now</b> — watch "
                    'progress on the <a href="/ui/refresh">Sync page</a>. Unused imports stay '
                    "on your account for whatever you save next.</div>"
                    '<p><a href="/ui/billing">Back to billing</a></p>')

    @app.post("/billing/webhook")
    async def billing_webhook_route(request: Request):
        cfg = Config.from_env()
        payload = await request.body()
        sig = request.headers.get("stripe-signature", "")
        try:
            billing.construct_event(payload, sig, cfg.stripe_webhook_secret)  # verify signature
        except Exception:
            return Response(status_code=400)  # bad signature / malformed → reject
        # Use the parsed payload (a real stripe Event object intercepts .get()); signature is verified.
        event = json.loads(payload)
        con = connect(cfg.database_url, cfg.tenant_id)  # owner; accounts is not RLS-scoped
        try:
            if not _apply_payment_event(con, event):          # one-time (import/credits/legacy)?
                if not _apply_invoice_event(con, event):       # monthly credit-sub grant?
                    info = billing.summarize_event(event)      # else subscription lifecycle
                    if info:
                        _apply_billing_event(con, info)
        finally:
            con.close()
        return {"received": True}

    # --- search (#4) ---
    @app.get("/search")
    def search_route(q: str, k: int = 10, con=Depends(get_db), ai=Depends(get_ai)):
        return {"query": q, "results": search(con, ai, q, k)}

    @app.post("/index")
    def index_route(con=Depends(get_db), ai=Depends(get_ai)):
        return {"indexed": index_posts(con, ai)}

    # --- taxonomy review (#5) ---
    @app.post("/taxonomy/derive")
    def derive_route(con=Depends(get_db), ai=Depends(get_ai)):
        return {"proposed": categorize.derive_taxonomy(con, ai)}

    @app.get("/taxonomy")
    def taxonomy_route(con=Depends(get_db)):
        return {"categories": categorize.get_taxonomy(con)}

    @app.post("/taxonomy")
    def save_taxonomy_route(body: TaxonomyIn, con=Depends(get_db)):
        categorize.save_taxonomy(con, [c.model_dump() for c in body.categories])
        return {"categories": categorize.get_taxonomy(con)}

    @app.post("/taxonomy/{category_id}/rename")
    def rename_route(category_id: int, body: RenameIn, con=Depends(get_db)):
        categorize.rename_category(con, category_id, body.name)
        return {"categories": categorize.get_taxonomy(con)}

    @app.delete("/taxonomy/{category_id}")
    def delete_route(category_id: int, con=Depends(get_db)):
        categorize.delete_category(con, category_id)
        return {"categories": categorize.get_taxonomy(con)}

    @app.post("/taxonomy/merge")
    def merge_route(source: int, target: int, con=Depends(get_db)):
        categorize.merge_categories(con, source, target)
        return {"categories": categorize.get_taxonomy(con)}

    # --- assignment + browse (#6) ---
    @app.post("/assign")
    def assign_route(con=Depends(get_db), ai=Depends(get_ai)):
        return {"processed": categorize.assign_unassigned(con, ai)}

    @app.get("/categories")
    def categories_route(con=Depends(get_db)):
        return {"categories": categorize.categories_with_counts(con)}

    @app.get("/categories/{category_id}")
    def category_detail_route(category_id: int, con=Depends(get_db)):
        return {"posts": categorize.posts_in_category(con, category_id)}

    # --- ask / RAG (#7) ---
    @app.post("/ask")
    def ask_route(body: AskIn, con=Depends(get_db), ai=Depends(get_ai)):
        cfg = Config.from_env()
        try:
            return credits.ask_charged(con, ai, body.question, body.k, cfg.ask_price_usd,
                                       cfg.free_asks_per_day, history=body.history)
        except credits.OutOfCredits:
            return {"question": body.question, "citations": [], "retrieved": [],
                    "answer": f"You've used today's {cfg.free_asks_per_day} free questions and "
                              f"your credit balance is empty. Each extra question costs "
                              f"${cfg.ask_price_usd:.2f} — top up on the Billing page, or come "
                              f"back tomorrow for {cfg.free_asks_per_day} more free ones."}

    # HTML screens (issues #4–#7 UI), wired to the same logic + dependencies.
    app.include_router(ui_router)

    # Landing-page screenshots etc. (packaged in xbb/static; /static is auth-exempt below).
    static_dir = Path(__file__).parent / "static"
    if static_dir.is_dir():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # When REQUIRE_AUTH is on (hosted/multi-user), gate everything behind a valid session except
    # the public surface (login, the OAuth/auth endpoints, the Stripe webhook, health).
    _PUBLIC_EXACT = {"/health", "/login", "/terms", "/privacy", "/",  # "/" shows the landing page
                     "/sitemap.xml", "/robots.txt"}
    _PUBLIC_PREFIX = ("/auth/", "/oauth/", "/static/")

    @app.middleware("http")
    async def _auth_gate(request: Request, call_next):
        cfg = Config.from_env()
        if cfg.require_auth:
            path = request.url.path
            public = (path in _PUBLIC_EXACT or path == "/billing/webhook"
                      or path.startswith(_PUBLIC_PREFIX))
            if not public:
                token = request.cookies.get(SESSION_COOKIE)
                if not (token and auth.verify_session_token(token, cfg.session_secret)):
                    return RedirectResponse("/login", status_code=303)
        return await call_next(request)

    @app.middleware("http")
    async def _pageview_log(request: Request, call_next):
        """Server-side page-view events (no JS, no tracking cookies — consistent with the
        privacy policy's "usage records"): which screens each account visits, so we can see
        whether users browse the feed/categories or ask-and-bounce. Paths + tenant ids only."""
        response = await call_next(request)
        path = request.url.path
        if request.method == "GET" and response.status_code == 200:
            try:
                if path == "/":
                    logger.info("ui.view page=landing")  # anonymous funnel top
                elif path.startswith("/ui/"):
                    cfg = Config.from_env()
                    tid = resolve_tenant(request, cfg)
                    logger.info("ui.view page=%s tenant=%s", path, tid)
            except Exception:
                pass  # a metrics line must never break a page
        return response

    return app


# Module-level app so `uvicorn xbb.web:app` works as documented in the README.
app = create_app()
