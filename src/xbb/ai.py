"""AI seam: all Amazon Bedrock calls behind one interface so tests can substitute it.

Covers the three AI jobs from docs/PRD.md:
  - taxonomy derivation + multi-label assignment (categorization)
  - embeddings (semantic search)
  - answer synthesis with citations (ask mode / RAG)
"""

from __future__ import annotations

from typing import Any, Protocol


class AIClient(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed texts via the Bedrock embedding model. (TODO.)"""
        ...

    def derive_taxonomy(self, samples: list[str]) -> list[dict[str, str]]:
        """Propose a starter taxonomy: [{name, definition}, ...]. (TODO.)"""
        ...

    def assign_categories(self, text: str, taxonomy: list[dict[str, str]]) -> list[str]:
        """Multi-label a post against the approved taxonomy. (TODO.)"""
        ...

    def answer(self, question: str, retrieved: list[dict[str, Any]]) -> dict[str, Any]:
        """Synthesize an answer with citations from retrieved bookmarks. (TODO.)"""
        ...
