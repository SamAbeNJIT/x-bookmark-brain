"""Local web app: search, ask, browse-by-category, and taxonomy review.

This module wires routes to the tested logic via dependency injection (`get_db`, `get_ai`)
so the endpoints are testable with a fake AI client. HTML screens are layered on next; for
now the routes return JSON.
"""

from __future__ import annotations

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from . import auth, authui, categorize, storage
from .ask import ask
from .config import Config
from .deps import SESSION_COOKIE, get_ai, get_db
from .search import index_posts, search
from .webui import ui_router


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
        # Dev: log the link (email delivery via SES lands with the deploy step).
        print(f"[auth] magic link for {email}: {link}", flush=True)
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
        return ask(con, ai, body.question, body.k)

    # HTML screens (issues #4–#7 UI), wired to the same logic + dependencies.
    app.include_router(ui_router)

    return app


# Module-level app so `uvicorn xbb.web:app` works as documented in the README.
app = create_app()
