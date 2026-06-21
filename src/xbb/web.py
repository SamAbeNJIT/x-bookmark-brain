"""Local web app: search, ask, browse-by-category, and taxonomy review.

This module wires routes to the tested logic via dependency injection (`get_db`, `get_ai`)
so the endpoints are testable with a fake AI client. HTML screens are layered on next; for
now the routes return JSON.
"""

from __future__ import annotations

from fastapi import Depends, FastAPI
from pydantic import BaseModel

from . import categorize
from .ask import ask
from .deps import get_ai, get_db
from .search import index_posts, search


class Category(BaseModel):
    name: str
    definition: str | None = None


class TaxonomyIn(BaseModel):
    categories: list[Category]


class RenameIn(BaseModel):
    name: str


class AskIn(BaseModel):
    question: str
    k: int = 8


def create_app() -> FastAPI:
    app = FastAPI(title="x-bookmark-brain")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

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

    return app


# Module-level app so `uvicorn xbb.web:app` works as documented in the README.
app = create_app()
