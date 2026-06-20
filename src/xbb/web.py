"""Local web app: search, ask, browse-by-category, and taxonomy review.

Scaffold only — routes are placeholders wired to the storage/ai layers in later slices.
Run (once implemented):  uvicorn xbb.web:app --reload
"""

from __future__ import annotations

try:
    from fastapi import FastAPI
except ImportError:  # scaffold: fastapi installed during the foundation slice
    FastAPI = None  # type: ignore


def create_app():  # pragma: no cover - scaffold
    if FastAPI is None:
        raise RuntimeError("Install dependencies first: pip install -e .[dev]")
    app = FastAPI(title="x-bookmark-brain")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    # TODO routes: GET /search, POST /ask, GET /categories, GET /categories/{id},
    #              taxonomy review (GET/POST /taxonomy)
    return app
