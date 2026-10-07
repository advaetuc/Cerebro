"""Offline tests for Last.fm spike helpers."""

import json
from collections import Counter

import httpx

from spike.lastfm_client import LastFmClient, LastFmResult, RateLimiter
from spike.probe_lastfm import (
    _build_vocabulary,
    _items,
    build_report,
    clean_tags,
    resolve_tags_with_fallback,
)


def test_tag_cleaning_filters_weak_self_and_junk_tags() -> None:
    tags = [
        {"name": "Radiohead", "count": 90},
        {"name": "seen live", "count": 99},
        {"name": "favorites", "count": 99},
        {"name": "weak", "count": 19},
        {"name": "alternative rock", "count": 20},
    ]
    assert clean_tags(tags, "Radiohead") == ["alternative rock"]


def test_lastfm_error_in_http_200_is_returned_as_typed_result(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"error": 29, "message": "Rate limit exceeded"})

    client = LastFmClient(
        "key", transport=httpx.MockTransport(handler), max_retries=0, cache_dir=tmp_path
    )
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


def test_top_artist_period_uses_period_query_parameter(tmp_path) -> None:
    observed: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed.append(dict(request.url.params))
        return httpx.Response(200, json={"topartists": {"artist": []}})

    client = LastFmClient(
        "key", transport=httpx.MockTransport(handler), max_retries=0, cache_dir=tmp_path
    )
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


def test_fallback_borrows_only_when_direct_tags_are_below_three() -> None:
    direct = [
        {"name": "dream pop", "count": 40},
        {"name": "indie", "count": 30},
        {"name": "alternative", "count": 25},
    ]
    similar = [("Similar Artist", [{"name": "shoegaze", "count": 50}])]
    direct_result, direct_borrowed = resolve_tags_with_fallback("Artist", direct, similar)
    assert not direct_borrowed
    assert "shoegaze" not in direct_result

    fallback_result, fallback_borrowed = resolve_tags_with_fallback(
        "Artist", direct[:2], similar
    )
    assert fallback_borrowed
    assert "shoegaze" in fallback_result


def test_borrowed_tag_weight_is_halved() -> None:
    tags, borrowed = resolve_tags_with_fallback(
        "Artist",
        [{"name": "direct one", "count": 24}],
        [("Similar Artist", [{"name": "borrowed mood", "count": 40}])],
    )
    assert borrowed
    assert tags["borrowed mood"] == 20.0


def test_singleton_tags_are_counted_but_excluded_from_vocabulary() -> None:
    vocabulary, singleton_count = _build_vocabulary(
        [
            {"tags": {"shared": 10.0, "rare": 20.0}},
            {"tags": {"shared": 5.0}},
        ]
    )
    assert singleton_count == 1
    assert vocabulary == [{"tag": "shared", "distinct_artist_count": 2, "weight": 15.0}]


def test_cache_hit_makes_zero_additional_http_calls(tmp_path) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"user": {"playcount": "12"}})

    first_client = LastFmClient(
        "key", transport=httpx.MockTransport(handler), max_retries=0, cache_dir=tmp_path
    )
    try:
        first_result = first_client.user_get_info("listener")
    finally:
        first_client.close()
    second_client = LastFmClient(
        "key", transport=httpx.MockTransport(handler), max_retries=0, cache_dir=tmp_path
    )
    try:
        second_result = second_client.user_get_info("listener")
    finally:
        second_client.close()
    assert first_result.data == second_result.data
    assert calls == 1
