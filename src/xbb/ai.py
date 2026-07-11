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
import re
import time
from typing import Any, Protocol


def _category_target(sample_count: int) -> int:
    """How many categories to derive for a corpus sample: ~1 per 20 bookmarks, floor of 4.
    The sample is capped at 500 (categorize.derive_taxonomy), so this tops out at ~25 —
    growth deliberately stops there (owner's call: chunky trees beat a million slivers)."""
    return max(4, sample_count // 20)


def _tax_names(taxonomy: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Taxonomy with only name + definition (drop the numeric id) so the labeler answers
    with category names, not ids — and to trim input tokens."""
    return [{"name": c["name"], "definition": c.get("definition")} for c in taxonomy]


def _norm_labels(raw: Any) -> list[dict[str, Any]]:
    """Normalize a model's label list to [{name, confidence}, ...].

    Accepts {"name", "confidence"} objects (the asked-for format) and bare strings (models
    occasionally regress to the old format under load) — a bare name means the model didn't
    hedge, so it gets confidence 1.0. Confidence is clamped to [0, 1]; malformed items dropped.
    """
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, str):
            out.append({"name": item, "confidence": 1.0})
        elif isinstance(item, dict) and isinstance(item.get("name"), str):
            try:
                conf = float(item.get("confidence", 1.0))
            except (TypeError, ValueError):
                conf = 1.0
            out.append({"name": item["name"], "confidence": min(max(conf, 0.0), 1.0)})
    return out


def _extract_json(text: str) -> Any:
    """Parse JSON from a model reply that may include prose or ```json fences.

    Claude on Bedrock often wraps a JSON array/object in explanation or markdown
    fences despite "reply with ONLY JSON" instructions. Strip fences, else fall back
    to the first balanced [...] / {...} span.
    """
    s = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", s, re.DOTALL)
    if fence:
        s = fence.group(1).strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    start = min((i for i in (s.find("["), s.find("{")) if i != -1), default=-1)
    end = max(s.rfind("]"), s.rfind("}"))
    if start != -1 and end > start:
        return json.loads(s[start : end + 1])
    raise ValueError(f"no JSON found in model reply: {text[:200]!r}")


class AIClient(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed texts for semantic search."""
        ...

    def derive_taxonomy(self, samples: list[str]) -> list[dict[str, str]]:
        """Propose a starter taxonomy: [{name, definition}, ...]."""
        ...

    def assign_categories(self, text: str, taxonomy: list[dict[str, str]]) -> list[dict[str, Any]]:
        """Multi-label a post against the approved taxonomy: [{name, confidence}, ...]."""
        ...

    def group_categories(self, names: list[str]) -> dict[str, str]:
        """Group category names into 4-8 parent themes: {category_name: parent_name}."""
        ...

    def assign_categories_batch(
        self, posts: list[dict[str, Any]], taxonomy: list[dict[str, str]]
    ) -> list[list[dict[str, Any]]]:
        """Multi-label several posts in one call. Returns labels aligned to `posts` order."""
        ...

    def rewrite_query(self, question: str, history: list[dict[str, str]]) -> str:
        """Rewrite a follow-up question into a standalone search query using the conversation."""
        ...

    def answer(
        self,
        question: str,
        retrieved: list[dict[str, Any]],
        history: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        """Synthesize an answer with citations from retrieved bookmarks; `history` is the
        prior conversation as [{role: user|assistant, content}, ...]."""
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
        self._usage: list[dict[str, Any]] = []  # token counts per call, drained by pop_usage()

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
                payload = json.loads(resp["body"].read())
                self._record_usage(model_id, payload)
                return payload
            except ClientError as e:
                code = e.response.get("Error", {}).get("Code", "")
                if code in {"ThrottlingException", "TooManyRequestsException",
                            "ServiceUnavailableException", "ModelTimeoutException"}:
                    time.sleep(delay)
                    delay = min(delay * 2, 30)
                    continue
                raise
        raise RuntimeError("Bedrock throttling: retries exhausted")

    def _record_usage(self, model_id: str, payload: dict[str, Any]) -> None:  # pragma: no cover
        """Accumulate token usage from a Bedrock response (Claude: usage{}, Titan: count)."""
        if not isinstance(payload, dict):
            return
        usage = payload.get("usage")
        if isinstance(usage, dict):  # Claude
            in_tok, out_tok = int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0))
        elif "inputTextTokenCount" in payload:  # Titan embeddings (no output tokens)
            in_tok, out_tok = int(payload.get("inputTextTokenCount", 0)), 0
        else:
            return
        self._usage.append({"model": model_id, "input_tokens": in_tok, "output_tokens": out_tok})

    def pop_usage(self) -> list[dict[str, Any]]:
        """Return and clear accumulated per-call token usage (drained once per request/job)."""
        events, self._usage = self._usage, []
        return events

    def embed(self, texts: list[str]) -> list[list[float]]:  # pragma: no cover
        # Amazon Titan Text Embeddings: one inputText per call (no batch endpoint).
        return [self._invoke(self.embedding_model, {"inputText": t})["embedding"] for t in texts]

    def _invoke_claude(
        self,
        model: str,
        system: str,
        user: str,
        max_tokens: int = 2048,
        history: list[dict[str, str]] | None = None,
    ) -> str:  # pragma: no cover
        payload = self._invoke(
            model,
            {
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": max_tokens,
                "system": system,
                "messages": [
                    {"role": t["role"], "content": t["content"]} for t in (history or [])
                ] + [{"role": "user", "content": user}],
            },
        )
        return payload["content"][0]["text"]

    def derive_taxonomy(self, samples: list[str]) -> list[dict[str, str]]:  # pragma: no cover
        target = _category_target(len(samples))
        system = (
            f"You organize a user's saved X posts. Propose about {target} categories (a couple "
            "more or fewer is fine) that cover the sample — broad enough that each holds several "
            "posts. Reply with ONLY a JSON array of {\"name\", \"definition\"} objects."
        )
        user = "Sample posts:\n" + "\n---\n".join(samples)
        return _extract_json(self._invoke_claude(self.reasoning_model, system, user))

    def group_categories(self, names: list[str]) -> dict[str, str]:  # pragma: no cover
        system = (
            "Group these bookmark categories into 4-8 broad parent themes (e.g. \"AI & "
            "Engineering\", \"Health & Longevity\", \"Culture & Media\"). Every category "
            "must appear in exactly one theme. Reply with ONLY a JSON object mapping "
            "{\"parent theme\": [\"category\", ...]}."
        )
        grouped = _extract_json(self._invoke_claude(
            self.labeling_model, system, "Categories:\n" + "\n".join(names)))
        out: dict[str, str] = {}
        for parent, kids in (grouped or {}).items():
            for kid in kids:
                if kid in names:
                    out[kid] = parent
        return out

    def assign_categories(self, text: str, taxonomy: list[dict[str, str]]) -> list[dict[str, Any]]:  # pragma: no cover
        system = (
            "Assign the post to one or more of the given categories — only categories it "
            "genuinely fits; do not force a fit. Reply with ONLY a JSON array of "
            "{\"name\", \"confidence\"} objects, name chosen strictly from the provided "
            "taxonomy and confidence between 0 and 1 (how well the post fits that category)."
        )
        user = f"Taxonomy: {json.dumps(_tax_names(taxonomy))}\n\nPost:\n{text}"
        try:
            result = _extract_json(self._invoke_claude(self.labeling_model, system, user))
        except ValueError:
            return []  # e.g. a URL-only post → model replies in prose, not JSON; no labels
        return _norm_labels(result)

    def assign_categories_batch(  # pragma: no cover
        self, posts: list[dict[str, Any]], taxonomy: list[dict[str, str]]
    ) -> list[list[dict[str, Any]]]:
        # Label many posts in ONE Claude call to cut cost/latency: the taxonomy (the bulk of
        # the input) is sent once per batch instead of once per post.
        if not posts:
            return []
        lines = []
        for i, p in enumerate(posts, 1):
            text = (p.get("text") or "").replace("\n", " ").strip()[:500]
            lines.append(f"[{i}] {text}")
        system = (
            "Multi-label each numbered post against the taxonomy — only categories a post "
            "genuinely fits; do not force a fit. Reply with ONLY a JSON object mapping each "
            "post number (as a string) to an array of {\"name\", \"confidence\"} objects, "
            "name chosen strictly from the taxonomy and confidence between 0 and 1 (how well "
            "the post fits). Include every number; use [] if none fit."
        )
        user = f"Taxonomy: {json.dumps(_tax_names(taxonomy))}\n\nPosts:\n" + "\n".join(lines)
        try:
            result = _extract_json(self._invoke_claude(self.labeling_model, system, user, max_tokens=4096))
        except ValueError:
            return [[] for _ in posts]
        out: list[list[dict[str, Any]]] = []
        for i in range(1, len(posts) + 1):
            v = result.get(str(i)) if isinstance(result, dict) else None
            out.append(_norm_labels(v))
        return out

    def rewrite_query(self, question: str, history: list[dict[str, str]]) -> str:  # pragma: no cover
        """Make a follow-up retrievable: "which of those are about evals?" only searches well
        once the conversation's referent is folded in. Cheap labeling-model call; on any
        failure fall back to the raw question (an ask must never die on the rewrite)."""
        if not history:
            return question
        system = (
            "Rewrite the user's latest question as a short standalone search query, resolving "
            "any references to the earlier conversation. Reply with ONLY the query text — no "
            "quotes, no explanation."
        )
        convo = "\n".join(f"{t['role']}: {t['content']}" for t in history)
        try:
            rewritten = self._invoke_claude(
                self.labeling_model, system,
                f"Conversation so far:\n{convo}\n\nLatest question: {question}",
                max_tokens=200,
            ).strip()
        except Exception:
            return question
        return rewritten or question

    def answer(
        self,
        question: str,
        retrieved: list[dict[str, Any]],
        history: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:  # pragma: no cover
        system = (
            "Answer the question using ONLY the provided saved posts, considering the prior "
            "conversation for context. Match your length to the question: answer as fully as "
            "it deserves, but don't pad. Cite the posts you use by their id. Reply with ONLY "
            "JSON: {\"answer\": str, \"citations\": [post_id]}."
        )
        # Send only what the model needs (id/handle/text) — the full dicts (urls, avatars,
        # media json) roughly double the input tokens for zero answer quality.
        slim = [{"id": r.get("id"), "handle": r.get("handle"),
                 "text": (r.get("text") or "")[:800]} for r in retrieved]
        user = f"Question: {question}\n\nPosts:\n{json.dumps(slim)}"
        raw = self._invoke_claude(self.reasoning_model, system, user, history=history)
        try:
            result = _extract_json(raw)
        except ValueError:
            result = None
        if not isinstance(result, dict):
            # Model answered in prose, not JSON — still show the text, just without citations.
            return {"answer": raw.strip(), "citations": []}
        result.setdefault("citations", [])
        return result
