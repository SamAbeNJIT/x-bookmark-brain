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
import time
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

        if getattr(self, "_rt", None) is None:
            self._rt = boto3.client("bedrock-runtime", region_name=self.region)
        return self._rt

    def _invoke(self, model_id: str, body: dict[str, Any]) -> dict[str, Any]:  # pragma: no cover
        """invoke_model with exponential backoff on Bedrock throttling/transient errors."""
        from botocore.exceptions import ClientError

        runtime = self._runtime()
        delay = 1.0
        for _ in range(7):
            try:
                resp = runtime.invoke_model(modelId=model_id, body=json.dumps(body))
                return json.loads(resp["body"].read())
            except ClientError as e:
                code = e.response.get("Error", {}).get("Code", "")
                if code in {"ThrottlingException", "TooManyRequestsException",
                            "ServiceUnavailableException", "ModelTimeoutException"}:
                    time.sleep(delay)
                    delay = min(delay * 2, 30)
                    continue
                raise
        raise RuntimeError("Bedrock throttling: retries exhausted")

    def embed(self, texts: list[str]) -> list[list[float]]:  # pragma: no cover
        # Amazon Titan Text Embeddings: one inputText per call (no batch endpoint).
        return [self._invoke(self.embedding_model, {"inputText": t})["embedding"] for t in texts]

    def _invoke_claude(self, model: str, system: str, user: str, max_tokens: int = 2048) -> str:  # pragma: no cover
        payload = self._invoke(
            model,
            {
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": max_tokens,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            },
        )
        return payload["content"][0]["text"]

    def derive_taxonomy(self, samples: list[str]) -> list[dict[str, str]]:  # pragma: no cover
        system = (
            "You organize a user's saved X posts. Propose 10-25 categories that cover the "
            "sample. Reply with ONLY a JSON array of {\"name\", \"definition\"} objects."
        )
        user = "Sample posts:\n" + "\n---\n".join(samples)
        return json.loads(self._invoke_claude(self.reasoning_model, system, user))

    def assign_categories(self, text: str, taxonomy: list[dict[str, str]]) -> list[str]:  # pragma: no cover
        system = (
            "Assign the post to one or more of the given categories. Reply with ONLY a JSON "
            "array of category names, chosen strictly from the provided taxonomy."
        )
        user = f"Taxonomy: {json.dumps(taxonomy)}\n\nPost:\n{text}"
        return json.loads(self._invoke_claude(self.labeling_model, system, user))

    def answer(self, question: str, retrieved: list[dict[str, Any]]) -> dict[str, Any]:  # pragma: no cover
        system = (
            "Answer the question using ONLY the provided saved posts. Cite the posts you "
            "use by their id. Reply with ONLY JSON: {\"answer\": str, \"citations\": [post_id]}."
        )
        user = f"Question: {question}\n\nPosts:\n{json.dumps(retrieved)}"
        return json.loads(self._invoke_claude(self.reasoning_model, system, user))
