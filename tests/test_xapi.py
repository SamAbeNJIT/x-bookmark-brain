from xbb import xapi


def _sample():
    tweet = {
        "id": "100",
        "author_id": "u1",
        "text": "hi #ai see https://t.co/x",
        "lang": "en",
        "created_at": "2026-06-01T00:00:00.000Z",
        "public_metrics": {"like_count": 5, "retweet_count": 2},
        "entities": {"hashtags": [{"tag": "ai"}], "urls": [{"expanded_url": "https://e.com"}]},
        "attachments": {"media_keys": ["m1"]},
        "referenced_tweets": [{"type": "quoted", "id": "99"}],
    }
    users = {"u1": {"id": "u1", "username": "alice", "name": "Alice", "profile_image_url": "http://img/a.jpg"}}
    media = {"m1": {"media_key": "m1", "type": "photo", "url": "http://pbs/m1.jpg", "alt_text": "chart"}}
    return tweet, users, media


def test_parse_v2_maps_core_fields():
    t, u, m = _sample()
    r = xapi.parse_bookmark_v2(t, u, m)
    assert r["id"] == "100"
    assert r["author"] == {"id": "u1", "handle": "alice", "display_name": "Alice", "avatar_url": "http://img/a.jpg"}
    assert r["url"] == "https://x.com/alice/status/100"
    assert r["kind"] == "quote" and r["parent_post_id"] == "99"
    assert r["media"][0]["url"] == "http://pbs/m1.jpg" and r["media"][0]["alt_text"] == "chart"
    assert r["hashtags"] == ["ai"] and r["links"] == ["https://e.com"]
    assert r["like_count"] == 5 and r["repost_count"] == 2


def test_parse_v2_note_tweet_text_wins():
    t, u, m = _sample()
    t["note_tweet"] = {"text": "the long-form text"}
    assert xapi.parse_bookmark_v2(t, u, m)["text"] == "the long-form text"


def test_parse_v2_reply_kind():
    t, u, m = _sample()
    t["referenced_tweets"] = [{"type": "replied_to", "id": "77"}]
    r = xapi.parse_bookmark_v2(t, u, m)
    assert r["kind"] == "reply" and r["parent_post_id"] == "77"
