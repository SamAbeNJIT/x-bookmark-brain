"""Robustness tests for the model-reply JSON extractor (the live AI path's parser)."""

import pytest

from xbb.ai import _extract_json, _norm_labels


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


def test_norm_labels_objects_and_clamping():
    assert _norm_labels([{"name": "AI", "confidence": 0.7},
                         {"name": "Crypto", "confidence": 1.7},
                         {"name": "Health", "confidence": -0.2}]) == [
        {"name": "AI", "confidence": 0.7},
        {"name": "Crypto", "confidence": 1.0},
        {"name": "Health", "confidence": 0.0},
    ]


def test_norm_labels_bare_strings_get_full_confidence():
    # Models occasionally regress to the pre-confidence format; a bare name means no hedge.
    assert _norm_labels(["AI", "Crypto"]) == [
        {"name": "AI", "confidence": 1.0},
        {"name": "Crypto", "confidence": 1.0},
    ]


def test_norm_labels_drops_malformed_items():
    assert _norm_labels([{"confidence": 0.9}, 42, None,
                         {"name": "AI", "confidence": "high"}]) == [
        {"name": "AI", "confidence": 1.0},  # unparseable confidence → benefit of the doubt
    ]
    assert _norm_labels("not a list") == []
