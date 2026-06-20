"""Tests against the ingestion seam (parsing), using recorded sample payloads.

These assert external behavior — the parsed record shape — not implementation details.
Marked xfail until the ingest-one slice lands.
"""

import pytest

from xbb.ingestion import parse_bookmark


@pytest.mark.xfail(reason="parse_bookmark implemented in the ingest-one slice", strict=False)
def test_parses_reply_with_parent_context():
    # Given a recorded reply payload (fixture TBD), the parsed record should expose the
    # bookmarked post AND its immediate parent's text.
    raw = {"kind": "reply"}  # placeholder fixture
    record = parse_bookmark(raw)
    assert record["kind"] == "reply"
    assert record["parent"]["text"]
