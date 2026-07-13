"""Pure tests for the Ask thread's accumulated-sources state (no DB, no AI).

The source groups ride a hidden, client-editable form field — trim_sources is the trust
boundary (like ask.trim_history), merge_sources the accumulate+dedupe rule.
"""

from xbb.webui import (
    SOURCES_MAX_GROUPS,
    SOURCES_MAX_IDS,
    _ask_form,
    merge_sources,
    trim_sources,
)


def test_trim_sources_rejects_garbage():
    assert trim_sources(None) == []
    assert trim_sources("[]") == []
    assert trim_sources([{"no_q": 1}, "x", {"q": "ok"}]) == []  # no ids -> dropped


def test_trim_sources_normalizes_and_bounds():
    groups = trim_sources(
        [{"q": "Q" * 500, "ids": list(range(100)), "cited": [1, 999]}]
    )
    assert len(groups) == 1
    g = groups[0]
    assert len(g["q"]) == 300
    assert len(g["ids"]) == SOURCES_MAX_IDS
    assert g["ids"][0] == "0"           # coerced to str
    assert g["cited"] == ["1"]          # 999 not in ids -> dropped
    many = trim_sources([{"q": f"q{i}", "ids": ["a"]} for i in range(10)])
    assert len(many) == SOURCES_MAX_GROUPS


def test_merge_sources_prepends_newest_and_dedupes():
    prior = [{"q": "first", "ids": ["1", "2", "3"], "cited": ["2"]}]
    groups = merge_sources(prior, "second", ["2", "4"], ["4"])
    assert groups[0] == {"q": "second", "ids": ["2", "4"], "cited": ["4"]}
    # "2" moved to the newest group; the old group keeps the rest, cited pruned with it
    assert groups[1] == {"q": "first", "ids": ["1", "3"], "cited": []}


def test_merge_sources_drops_emptied_groups():
    prior = [{"q": "first", "ids": ["1"], "cited": ["1"]}]
    groups = merge_sources(prior, "second", ["1", "2"], [])
    assert [g["q"] for g in groups] == ["second"]


def test_merge_roundtrips_through_trim():
    """What merge_sources emits must survive the next request's trim_sources unchanged
    (modulo the group cap) — it round-trips through the hidden field."""
    groups = merge_sources([], "q1", ["10", "11"], ["11"])
    assert trim_sources(groups) == groups


def test_ask_form_carries_sources_hidden_field():
    html = _ask_form("", history=[{"role": "user", "content": "hi"}],
                     sources=[{"q": "q1", "ids": ["1"], "cited": []}])
    assert 'name=sources' in html
    assert "q1" in html
    # and without sources there is no field
    assert "name=sources" not in _ask_form("fresh")
