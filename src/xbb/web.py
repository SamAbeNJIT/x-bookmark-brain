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

    @app.get("/search")
    def search_route(q: str, k: int = 10):  # pragma: no cover - live wiring (Bedrock + db)
        from .ai import BedrockAIClient
        from .config import Config
        from .search import search
        from .storage import connect

        cfg = Config.from_env()
        con = connect(cfg.db_path)
        try:
            ai = BedrockAIClient(
                region=cfg.aws_region,
                embedding_model=cfg.bedrock_embedding_model,
            )
            return {"query": q, "results": search(con, ai, q, k)}
        finally:
            con.close()

    # TODO routes: POST /ask, GET /categories, GET /categories/{id},
    #              taxonomy review (GET/POST /taxonomy)
    return app


# Module-level app so `uvicorn xbb.web:app` works as documented in the README.
app = create_app()
