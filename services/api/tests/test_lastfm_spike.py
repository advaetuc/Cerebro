"""Offline tests for Last.fm spike helpers."""

import json
from collections import Counter

import httpx

from spike.lastfm_client import LastFmClient, LastFmResult, RateLimiter
from spike.probe_lastfm import _items, build_report, clean_tags


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


def test_item_count_normal_list_fixture() -> None:
    result = _fixture_result({"topartists": {"artist": [{"name": "A"}, {"name": "B"}]}})
    assert len(_items(result, ("topartists", "artist"))) == 2


def test_item_count_single_item_dict_fixture() -> None:
    result = _fixture_result({"toptracks": {"track": {"name": "Track"}}})
    assert len(_items(result, ("toptracks", "track"))) == 1


def test_item_count_empty_list_fixture() -> None:
    result = _fixture_result({"topartists": {"artist": []}})
    assert _items(result, ("topartists", "artist")) == []


def test_recent_track_count_skips_nowplaying_fixture() -> None:
    result = _fixture_result(
        {
            "recenttracks": {
                "track": [
                    {"name": "Playing", "@attr": {"nowplaying": "true"}},
                    {"name": "Scrobbled"},
                ]
            }
        }
    )
    assert _items(result, ("recenttracks", "track")) == [{"name": "Scrobbled"}]


def test_top_artist_period_uses_period_query_parameter() -> None:
    observed: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed.append(dict(request.url.params))
        return httpx.Response(200, json={"topartists": {"artist": []}})

    client = LastFmClient("key", transport=httpx.MockTransport(handler), max_retries=0)
    try:
        result = client.user_top_artists("listener", "1month")
    finally:
        client.close()
    assert observed[0]["period"] == "1month"
    assert "range" not in observed[0]
    assert "time" not in observed[0]
    assert result.query_param_names == ("method", "api_key", "format", "user", "period", "limit")


def _fixture_result(data: dict[str, object]) -> LastFmResult:
    return LastFmResult("fixture", 200, data)
