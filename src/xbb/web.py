"""Local web app: search, ask, browse-by-category, and taxonomy review.

This module wires routes to the tested logic via dependency injection (`get_db`, `get_ai`)
so the endpoints are testable with a fake AI client. HTML screens are layered on next; for
now the routes return JSON.
"""

from __future__ import annotations

import json

from fastapi import Depends, FastAPI, Form, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from . import auth, authui, billing, categorize, credits, mail, storage
from .config import Config
from .deps import SESSION_COOKIE, get_ai, get_db
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
    """Handle a one-time payment (ingestion charge or credit pack). Returns True if it was a
    one-time payment we handled (so the caller skips the subscription path)."""
    if event.get("type") != "checkout.session.completed":
        return False
    obj = event.get("data", {}).get("object", {})
    if obj.get("mode") != "payment":
        return False  # subscription checkout — not ours
    account_id = obj.get("client_reference_id")
    kind = (obj.get("metadata") or {}).get("kind")
    if account_id and kind == "ingestion":
        storage.set_ingestion_paid(con, account_id, True)
    elif account_id and kind == "credits":
        amount = (obj.get("amount_total") or 0) / 100.0  # cents → USD; credits are 1:1 with $ paid
        if amount > 0:
            storage.add_credits(con, account_id, amount)
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


def create_app() -> FastAPI:
    app = FastAPI(title="x-bookmark-brain")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

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
        account_id = storage.get_or_create_account(con, email)
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
    def billing_page_route(request: Request, con=Depends(get_db)):
        cfg = Config.from_env()
        bal = storage.credit_balance(con)
        ingested = storage.is_ingestion_paid(con)
        asks = int(bal / cfg.ask_price_usd) if cfg.ask_price_usd else 0
        body = (f'<div class="answer"><b>Credit balance: ${bal:.2f}</b> '
                f"(~{asks} questions at ${cfg.ask_price_usd:.2f} each).</div>")
        if not ingested:
            if cfg.stripe_secret_key and cfg.stripe_ingest_price_id:
                body += ("<p class=lead>Import your X bookmarks to get started.</p>"
                         '<form method=post action="/billing/checkout">'
                         '<input type=hidden name=kind value="ingestion">'
                         f"<button>Import my bookmarks — ${cfg.ingestion_price_usd:.2f}</button></form>")
            else:
                body += "<p class=muted>Ingestion checkout isn't configured.</p>"
        else:
            body += "<p class=muted>✓ Bookmarks imported.</p>"
        if cfg.stripe_secret_key and cfg.stripe_credit_price_id:
            body += ('<form method=post action="/billing/checkout" style="margin-top:1rem">'
                     '<input type=hidden name=kind value="credits">'
                     "<button>Buy credits</button></form>")
        else:
            body += "<p class=muted>Credit purchase isn't configured.</p>"
        return page("Billing", body)

    @app.post("/billing/checkout")
    def checkout_route(request: Request, kind: str = Form("credits"), con=Depends(get_db)):
        cfg = Config.from_env()
        price_id = cfg.stripe_ingest_price_id if kind == "ingestion" else cfg.stripe_credit_price_id
        if not (cfg.stripe_secret_key and price_id):
            return RedirectResponse("/ui/billing", status_code=303)
        account_id = _current_account(request, cfg)
        email = storage.get_account_email(con, account_id) or "local@bookmarkbrain.app"
        base = str(request.base_url).rstrip("/")
        url = billing.create_payment_session(
            api_key=cfg.stripe_secret_key, price_id=price_id,
            customer_email=email, client_reference_id=account_id,
            success_url=base + "/billing/success", cancel_url=base + "/ui/billing",
            metadata={"kind": kind, "account_id": account_id},
        )
        return RedirectResponse(url, status_code=303)

    @app.get("/billing/success")
    def billing_success_route():
        return page("Billing", '<div class="answer">🎉 Thanks! Your purchase is being applied — '
                    "your balance / import status updates here in a moment.</div>"
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
            if not _apply_payment_event(con, event):          # one-time (ingestion / credits)?
                info = billing.summarize_event(event)          # else subscription lifecycle
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
            return credits.ask_charged(con, ai, body.question, body.k, cfg.ask_price_usd)
        except credits.OutOfCredits:
            return {"question": body.question, "citations": [], "retrieved": [],
                    "answer": f"You're out of credits. Each question costs "
                              f"${cfg.ask_price_usd:.2f} — top up on the Billing page to continue."}

    # HTML screens (issues #4–#7 UI), wired to the same logic + dependencies.
    app.include_router(ui_router)

    # When REQUIRE_AUTH is on (hosted/multi-user), gate everything behind a valid session except
    # the public surface (login, the OAuth/auth endpoints, the Stripe webhook, health).
    _PUBLIC_EXACT = {"/health", "/login"}
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

    return app


# Module-level app so `uvicorn xbb.web:app` works as documented in the README.
app = create_app()
