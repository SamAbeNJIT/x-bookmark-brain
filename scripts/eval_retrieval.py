"""Retrieval-quality eval harness (backlog item 15).

Scores retrieval for a fixed question set against the owner corpus using LLM-judged
relevance: each (question, post) pair in the top-k is judged relevant yes/no by the
labeling model (Haiku), and judgments are CACHED on disk so re-runs and A/B variants
(baseline vs reranker vs new embeddings) are cheap and comparable. Metric: precision@k
plus judged-relevant counts. Read-only against prod (owner tenant); costs pennies.

Usage:
    python scripts/eval_retrieval.py baseline          # current hybrid search
    python scripts/eval_retrieval.py rerank            # hybrid 100 -> cohere rerank -> top k
    python scripts/eval_retrieval.py compare           # print cached results side by side
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from xbb import storage  # noqa: E402
from xbb.ai import BedrockAIClient, _extract_json  # noqa: E402
from xbb.search import search  # noqa: E402

OWNER_TENANT = "302af52a-83bd-45e6-97d9-f33751aa1ec1"
K = 30          # what the ask flow actually consumes
POOL = 100      # candidates fetched before reranking
CACHE_PATH = os.path.join(os.path.dirname(__file__), "eval_judgments.json")
RESULTS_PATH = os.path.join(os.path.dirname(__file__), "eval_results.json")

# Owner-editable. Spread across the corpus's top categories; phrased the way a real
# user asks (vague, meaning-based), not like keyword search.
QUESTIONS = [
    "What did I save about running AI models locally?",
    "Which bookmarks compare AI model benchmarks, like Claude versus GPT?",
    "What advice did I bookmark about building AI coding agents?",
    "What did I save about prompt engineering or agentic workflow tips?",
    "What did I save about peptides or nootropics?",
    "What are the bookmarked takes on Ethereum as an investment?",
    "Which books were recommended in my bookmarks?",
    "What did I save about testosterone, hormones, or blood work?",
    "What threads did I save about American manufacturing and industry?",
    "What did I bookmark about dating dynamics?",
    "What wisdom or stoic quotes did I save?",
    "What did I save about diet controversies, like seed oils?",
    "What stock picks or investment theses did I bookmark?",
    "What did I save about Christianity or criticism of churches?",
    "What funny posts about programmers did I save?",
]


def _judge(ai: BedrockAIClient, question: str, text: str) -> bool:
    system = (
        "You judge search relevance for a personal bookmark library. Given a user's "
        "question and one saved post, answer whether the post is genuinely relevant to "
        "the question. Reply with ONLY JSON: {\"relevant\": true|false}."
    )
    raw = ai._invoke_claude(ai.labeling_model, system,
                            f"Question: {question}\n\nPost:\n{text[:600]}", max_tokens=30)
    try:
        return bool(_extract_json(raw).get("relevant"))
    except Exception:
        return False


def _load(path: str) -> dict:
    return json.load(open(path)) if os.path.exists(path) else {}


def run(variant: str) -> None:
    cache = _load(CACHE_PATH)
    results = _load(RESULTS_PATH)
    con = storage.connect(os.environ["DATABASE_URL"], OWNER_TENANT)
    ai = BedrockAIClient(os.environ["AWS_REGION"],
                         embedding_model=os.environ["BEDROCK_EMBEDDING_MODEL"],
                         labeling_model=os.environ["BEDROCK_LABELING_MODEL"],
                         reasoning_model=os.environ["BEDROCK_REASONING_MODEL"])
    per_q = {}
    for q in QUESTIONS:
        if variant == "baseline":
            hits = search(con, ai, q, K)
        elif variant == "rerank":
            from xbb.rerank import rerank  # exists once the reranker lands
            hits = rerank(ai, q, search(con, ai, q, POOL), K)
        else:
            raise SystemExit(f"unknown variant {variant!r}")
        relevant = 0
        for p in hits:
            key = f"{q}|{p['id']}"
            if key not in cache:
                cache[key] = _judge(ai, q, p.get("text") or "")
                json.dump(cache, open(CACHE_PATH, "w"))
            relevant += bool(cache[key])
        per_q[q] = {"relevant": relevant, "k": len(hits)}
        print(f"  {relevant:2d}/{len(hits):2d} relevant  {q}")
    con.close()
    mean_p = sum(v["relevant"] / max(v["k"], 1) for v in per_q.values()) / len(per_q)
    print(f"\n{variant}: mean precision@{K} = {mean_p:.2f}")
    results[variant] = {"mean_precision": round(mean_p, 3), "k": K, "per_question": per_q}
    json.dump(results, open(RESULTS_PATH, "w"), indent=1)


def compare() -> None:
    results = _load(RESULTS_PATH)
    for name, r in results.items():
        print(f"{name}: mean precision@{r['k']} = {r['mean_precision']}")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "baseline"
    compare() if mode == "compare" else run(mode)
