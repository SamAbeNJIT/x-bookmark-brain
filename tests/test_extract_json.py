"""Robustness tests for the model-reply JSON extractor (the live AI path's parser)."""

import pytest

from xbb.ai import _extract_json


def test_plain_json_array():
    assert _extract_json('["AI", "Crypto"]') == ["AI", "Crypto"]


def test_fenced_json():
    assert _extract_json("```json\n[\"AI\"]\n```") == ["AI"]


def test_prose_wrapped_object():
    raw = 'Sure! Here you go: {"answer": "x", "citations": ["1"]} — hope that helps.'
    assert _extract_json(raw) == {"answer": "x", "citations": ["1"]}


def test_unparseable_raises_valueerror():
    # assign_categories / answer rely on this contract to fall back gracefully.
    with pytest.raises(ValueError):
        _extract_json("I can't help with that, sorry.")
