"""Second-stage reranking for retrieval (backlog item 15).

Hybrid search casts a wide net (top-POOL candidates by RRF); the reranker reads the
query and each candidate TOGETHER with a cross-encoder (Cohere Rerank 3.5 on Bedrock)
and keeps the k that actually answer it. Embedding distance can't see "is this post a
book recommendation?" — a cross-encoder can. ~0.1¢ per query, ~200ms.

Best-effort by design: any rerank failure returns the original hybrid order — a ranking
upgrade must never break search or ask.
"""

from __future__ import annotations

import json
from typing import Any

RERANK_MODEL = "cohere.rerank-v3-5:0"


def rerank(ai: Any, query: str, candidates: list[dict[str, Any]], k: int) -> list[dict[str, Any]]:
    """Reorder hybrid-search candidates by cross-encoder relevance; return the top k."""
    if len(candidates) <= 1:
        return candidates[:k]
    docs = [(c.get("text") or "")[:1000] for c in candidates]
    try:
        payload = ai._invoke(RERANK_MODEL, {
            "api_version": 2,
            "query": query[:1000],
            "documents": docs,
            "top_n": min(k, len(docs)),
        })
        order = [r["index"] for r in payload["results"]]
    except Exception:
        return candidates[:k]  # degraded, not broken
    return [candidates[i] for i in order if 0 <= i < len(candidates)]
