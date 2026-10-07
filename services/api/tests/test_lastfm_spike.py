"""Offline tests for Last.fm spike helpers."""

import json
from collections import Counter

import httpx

from spike.lastfm_client import LastFmClient, RateLimiter
from spike.probe_lastfm import build_report, clean_tags


def test_tag_cleaning_filters_weak_self_and_junk_tags() -> None:
    tags = [
        {"name": "Radiohead", "count": 90},
        {"name": "seen live", "count": 99},
        {"name": "favorites", "count": 99},
        {"name": "weak", "count": 19},
        {"name": "alternative rock", "count": 20},
    ]
    assert clean_tags(tags, "Radiohead") == ["alternative rock"]


def test_lastfm_error_in_http_200_is_returned_as_typed_result() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"error": 29, "message": "Rate limit exceeded"})

    client = LastFmClient("key", transport=httpx.MockTransport(handler), max_retries=0)
    try:
        result = client.user_get_info("private-user")
    finally:
        client.close()
    assert result.status == 200
    assert result.error_code == 29
    assert not result.ok


def test_report_redacts_artist_track_and_user_names() -> None:
    report = build_report(
        [
            {
                "endpoint": "user.getInfo/private-user/Radiohead/Track Title",
                "status": 200,
                "error_code": None,
                "latency_ms": 1,
                "item_count": 1,
                "username": "private-user",
                "artist_name": "Radiohead",
                "track_name": "Track Title",
            }
        ],
        [["ambient"]],
        1,
        Counter({"ambient": 1}),
    )
    serialized = json.dumps(report).casefold()
    assert "private-user" not in serialized
    assert "radiohead" not in serialized
    assert "track title" not in serialized
    assert "artist_name" not in serialized


def test_rate_limiter_spaces_calls(monkeypatch) -> None:
    now = [0.0]
    waits: list[float] = []

    def sleep(delay: float) -> None:
        waits.append(delay)
        now[0] += delay

    monkeypatch.setattr("spike.lastfm_client.time.monotonic", lambda: now[0])
    monkeypatch.setattr("spike.lastfm_client.time.sleep", sleep)
    limiter = RateLimiter(4)
    limiter.wait()
    limiter.wait()
    limiter.wait()
    assert waits == [0.25, 0.25]
