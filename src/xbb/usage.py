"""Billing/metering math — pure, no AWS, no DB, no env.

`cost_of` turns a token count into a dollar amount using Amazon Bedrock on-demand rates;
`within_quota` is the spend-cap predicate. Both are plain functions so they're trivial to
unit-test and to plug into the AI seam's metering next.

Rates are USD per 1,000,000 tokens (input, output), matching Bedrock on-demand pricing.
They live in one table so updating a price is a one-line change. Model ids are matched by
substring, so cross-region inference-profile prefixes (``us.``/``eu.``/``apac.``), the
``anthropic.``/``amazon.`` provider prefixes, and version suffixes (``:0``) all resolve to
the same rate.
"""

from __future__ import annotations

# (input_per_1M, output_per_1M) in USD. Embedding models have no output tokens.
BEDROCK_RATES_PER_1M: dict[str, tuple[float, float]] = {
    # Claude (text generation)
    "claude-opus-4-8": (5.00, 25.00),
    "claude-opus-4-7": (5.00, 25.00),
    "claude-sonnet-5": (2.00, 10.00),   # from the Bedrock agreement rate card (cheaper than 4-6)
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
    # xAI Grok on the bedrock-mantle endpoint
    "grok-4.3": (1.25, 2.50),
    # Amazon Titan Text Embeddings (input only)
    "titan-embed-text-v2": (0.02, 0.00),
    "titan-embed-text-v1": (0.10, 0.00),
}


def _rates_for(model: str) -> tuple[float, float]:
    key = model.lower()
    for name, rates in BEDROCK_RATES_PER_1M.items():
        if name in key:
            return rates
    raise ValueError(f"no Bedrock pricing known for model {model!r}")


def cost_of(model: str, input_tokens: int, output_tokens: int = 0) -> float:
    """USD cost of a Bedrock call given its token counts.

    `model` may be any form of the id (bare, provider-prefixed, or an inference-profile id);
    it's matched to a rate by substring. Raises ValueError if the model isn't priced.
    """
    in_rate, out_rate = _rates_for(model)
    return (input_tokens * in_rate + output_tokens * out_rate) / 1_000_000


def within_quota(used: float, limit: float | None) -> bool:
    """True if `used` spend is within `limit` (i.e. has not exceeded it).

    `limit` of None means unlimited. To gate a call before making it, pass the projected
    post-call total: ``within_quota(used + cost_of(...), limit)``.
    """
    if limit is None:
        return True
    return used <= limit
