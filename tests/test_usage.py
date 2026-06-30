"""Pure unit tests for usage.py — no DB, no DATABASE_URL, no fixtures."""

import pytest

from xbb.usage import cost_of, within_quota


def test_cost_of_claude_haiku():
    # 1M input @ $1 + 1M output @ $5 = $6.00
    assert cost_of("anthropic.claude-haiku-4-5", 1_000_000, 1_000_000) == pytest.approx(6.00)


def test_cost_of_resolves_inference_profile_prefix():
    # us.anthropic.claude-sonnet-4-6-...  → Sonnet rate ($3 / $15 per 1M)
    assert cost_of("us.anthropic.claude-sonnet-4-6", 2_000_000, 0) == pytest.approx(6.00)
    assert cost_of("us.anthropic.claude-sonnet-4-6", 0, 1_000_000) == pytest.approx(15.00)


def test_cost_of_embeddings_has_no_output_charge():
    # Titan v2: $0.02 / 1M input, output tokens are free even if passed.
    assert cost_of("amazon.titan-embed-text-v2:0", 1_000_000, 9_999) == pytest.approx(0.02)


def test_cost_of_zero_tokens_is_zero():
    assert cost_of("claude-opus-4-8", 0, 0) == 0.0


def test_cost_of_unknown_model_raises():
    with pytest.raises(ValueError):
        cost_of("openai-gpt-9", 100, 100)


def test_within_quota_boundaries():
    assert within_quota(5.0, 10.0) is True
    assert within_quota(10.0, 10.0) is True   # exactly at the cap is still within
    assert within_quota(10.01, 10.0) is False


def test_within_quota_none_limit_is_unlimited():
    assert within_quota(1_000_000.0, None) is True
