"""Parser contract tests over synthetic-but-realistic X GraphQL payloads.

These pin parse_bookmark's external behavior (the record shape) without hitting X.
"""

from __future__ import annotations

from xbb.ingestion import parse_bookmark


def _tweet(rest_id, full_text, *, handle="alice", name="Alice", legacy_extra=None, extra=None):
    legacy = {
        "full_text": full_text,
        "lang": "en",
        "created_at": "Wed Jun 18 12:00:00 +0000 2025",
        "favorite_count": 5,
        "retweet_count": 1,
        "entities": {"hashtags": [], "urls": []},
    }
    legacy.update(legacy_extra or {})
    node = {
        "__typename": "Tweet",
        "rest_id": rest_id,
        "core": {
            "user_results": {
                "result": {
                    "rest_id": f"u_{handle}",
                    "core": {"screen_name": handle, "name": name},
                    "legacy": {"screen_name": handle, "name": name},
                }
            }
        },
        "legacy": legacy,
    }
    node.update(extra or {})
    return node


def _entry(tweet):
    return {
        "entryId": f"tweet-{tweet['rest_id']}",
        "content": {
            "entryType": "TimelineTimelineItem",
            "itemContent": {"itemType": "TimelineTweet", "tweet_results": {"result": tweet}},
        },
    }


def test_parses_original_post():
    rec = parse_bookmark(_entry(_tweet("100", "hello world #ai", legacy_extra={
        "entities": {"hashtags": [{"text": "ai"}], "urls": [{"expanded_url": "https://e.com"}]},
    })))
    assert rec["id"] == "100"
    assert rec["kind"] == "original"
    assert rec["text"] == "hello world #ai"
    assert rec["author"]["handle"] == "alice"
    assert rec["url"] == "https://x.com/alice/status/100"
    assert rec["hashtags"] == ["ai"]
    assert rec["links"] == ["https://e.com"]
    assert rec["parent"] is None


def test_parses_reply_keeps_parent_id():
    rec = parse_bookmark(_entry(_tweet("200", "exactly right", legacy_extra={
        "in_reply_to_status_id_str": "199",
    })))
    assert rec["kind"] == "reply"
    assert rec["parent_post_id"] == "199"
    assert rec["parent"]["id"] == "199"


def test_parses_quote_with_inline_quoted_post():
    quoted = _tweet("300", "the original claim", handle="bob", name="Bob")
    rec = parse_bookmark(_entry(_tweet("301", "this is wrong", legacy_extra={
        "is_quote_status": True,
    }, extra={"quoted_status_result": {"result": quoted}})))
    assert rec["kind"] == "quote"
    assert rec["parent_post_id"] == "300"
    assert rec["parent"]["text"] == "the original claim"
    assert rec["parent"]["author"]["handle"] == "bob"


def test_visibility_wrapper_is_unwrapped():
    inner = _tweet("400", "behind a visibility wrapper")
    wrapped = {"__typename": "TweetWithVisibilityResults", "tweet": inner}
    entry = {
        "content": {
            "entryType": "TimelineTimelineItem",
            "itemContent": {"itemType": "TimelineTweet", "tweet_results": {"result": wrapped}},
        }
    }
    rec = parse_bookmark(entry)
    assert rec["id"] == "400"
    assert rec["text"] == "behind a visibility wrapper"


def test_longform_note_tweet_text_wins():
    long_text = "x" * 500
    rec = parse_bookmark(_entry(_tweet("500", "truncated...", extra={
        "note_tweet": {"note_tweet_results": {"result": {"text": long_text}}},
    })))
    assert rec["text"] == long_text
