"""AI seam: all Amazon Bedrock calls behind one interface so tests can substitute it.

Covers the three AI jobs from docs/PRD.md:
  - taxonomy derivation + multi-label assignment (categorization)
  - embeddings (semantic search)
  - answer synthesis with citations (ask mode / RAG)

`BedrockAIClient` is the live implementation; it needs AWS credentials + Bedrock model
access, so it is not exercised by the test suite (tests pass a fake implementing the same
interface).
"""

from __future__ import annotations

import json
from typing import Any, Protocol


class AIClient(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed texts for semantic search."""
        ...

    def derive_taxonomy(self, samples: list[str]) -> list[dict[str, str]]:
        """Propose a starter taxonomy: [{name, definition}, ...]."""
        ...

    def assign_categories(self, text: str, taxonomy: list[dict[str, str]]) -> list[str]:
        """Multi-label a post against the approved taxonomy."""
        ...

    def answer(self, question: str, retrieved: list[dict[str, Any]]) -> dict[str, Any]:
        """Synthesize an answer with citations from retrieved bookmarks."""
        ...


class BedrockAIClient:
    """Live Amazon Bedrock client. Not covered by tests (needs AWS creds + model access)."""

    def __init__(
        self,
        region: str,
        embedding_model: str | None = None,
        labeling_model: str | None = None,
        reasoning_model: str | None = None,
    ) -> None:
        self.region = region
        self.embedding_model = embedding_model
        self.labeling_model = labeling_model
        self.reasoning_model = reasoning_model

    def _runtime(self):  # pragma: no cover
        import boto3

        return boto3.client("bedrock-runtime", region_name=self.region)

    def embed(self, texts: list[str]) -> list[list[float]]:  # pragma: no cover
        runtime = self._runtime()
        vectors: list[list[float]] = []
        for text in texts:
            # Amazon Titan Text Embeddings request/response shape.
            resp = runtime.invoke_model(
                modelId=self.embedding_model,
                body=json.dumps({"inputText": text}),
            )
            payload = json.loads(resp["body"].read())
            vectors.append(payload["embedding"])
        return vectors

    def derive_taxonomy(self, samples: list[str]) -> list[dict[str, str]]:  # pragma: no cover
        raise NotImplementedError("taxonomy derivation slice (#5)")

    def assign_categories(self, text: str, taxonomy: list[dict[str, str]]) -> list[str]:  # pragma: no cover
        raise NotImplementedError("assignment slice (#6)")

    def answer(self, question: str, retrieved: list[dict[str, Any]]) -> dict[str, Any]:  # pragma: no cover
        raise NotImplementedError("ask slice (#7)")
