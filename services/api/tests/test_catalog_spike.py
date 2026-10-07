"""Offline tests for catalog query construction and caching helpers."""

import asyncio
import inspect

import httpx

from app.services.catalog.clients import (
    AsyncRateLimiter,
    AsyncTokenBucket,
    CatalogClients,
    CatalogResult,
    ConfigError,
    JsonDiskCache,
    build_apicalypse,
    build_headers,
    build_multiquery,
    build_tmdb_params,
    select_keyword_match,
    tmdb_backoff_delay,
    token_is_valid,
)
from app.services.catalog.retrieval import (
    INTENTS,
    _dedupe,
    _fetch_intent,
    _igdb_queries,
    _jaccard_report,
    _table_lines,
    _tmdb_queries,
)


def test_intent_names_resolve_to_runtime_query_ids() -> None:
    intent = INTENTS["Neon Insomniac"]
    ids = {
        "tmdb_genres": {"science fiction": 878, "thriller": 53},
        "tmdb_keywords": {
            name.casefold(): index
            for index, name in enumerate(intent["tmdb_keywords"], 10)
        },
        "igdb_genres": {
            name.casefold(): index
            for index, name in enumerate(intent["igdb_genres"], 20)
        },
        "igdb_themes": {
            name.casefold(): index
            for index, name in enumerate(intent["igdb_themes"], 30)
        },
    }
    movie_queries = _tmdb_queries(intent, ids, "a")
    game_queries = _igdb_queries(intent, ids)
    assert len(movie_queries) == 2
    assert len(game_queries) == 3
    assert all("with_genres" in query and "with_keywords" in query for query in movie_queries)
    assert all("total_rating_count >= 50" in query for query in game_queries)


def test_tmdb_query_builder_uses_resolved_ids_and_vote_floor() -> None:
    query = build_tmdb_params([878], [42])
    assert query["with_genres"] == "878"
    assert query["with_keywords"] == "42"
    assert query["vote_count.gte"] == 500
    assert query["vote_average.gte"] == 6
    assert query["sort_by"] == "popularity.desc"
    assert build_tmdb_params([1, 2], [3, 4])["with_genres"] == "1|2"
    assert build_tmdb_params([1, 2], [3, 4])["with_keywords"] == "3|4"


def test_apicalypse_body_is_stable() -> None:
    assert build_apicalypse([12], [31]) == (
        "fields id,name,first_release_date,total_rating,total_rating_count,genres,themes,"
        "cover.image_id; "
        "where total_rating_count >= 50 & genres = (12) & themes = (31); "
        "sort total_rating desc; limit 40;"
    )


def test_token_expiry_reserves_sixty_seconds() -> None:
    assert token_is_valid(1_061, now=1_000)
    assert not token_is_valid(1_060, now=1_000)
    assert not token_is_valid(999, now=1_000)


def test_header_helper_cleans_wrappers_and_rejects_non_ascii() -> None:
    assert build_headers({"Authorization": ("TMDB_READ_TOKEN", ' "abc" ')}) == {
        "Authorization": "abc"
    }
    try:
        build_headers({"Authorization": ("TMDB_READ_TOKEN", "bad…token")})
    except ConfigError as error:
        assert "TMDB_READ_TOKEN" in str(error)
        assert "re-copy the full token" in str(error)
    else:
        raise AssertionError("non-ASCII header value should be rejected")


def test_async_limiter_spaces_requests(monkeypatch) -> None:
    now = [0.0]
    waits: list[float] = []

    async def sleep(delay: float) -> None:
        waits.append(delay)
        now[0] += delay

    monkeypatch.setattr("app.services.catalog.clients.time.monotonic", lambda: now[0])
    monkeypatch.setattr("app.services.catalog.clients.asyncio.sleep", sleep)

    async def exercise() -> None:
        limiter = AsyncRateLimiter(4)
        await limiter.wait()
        await limiter.wait()
        await limiter.wait()

    asyncio.run(exercise())
    assert waits == [0.25, 0.25]


def test_candidate_results_dedupe_by_id() -> None:
    results = [
        CatalogResult("games", 200, [{"id": 5, "name": "A"}, {"id": 6, "name": "B"}]),
        CatalogResult("games", 200, [{"id": 5, "name": "A duplicate"}]),
    ]
    unique = _dedupe(results, "")
    assert [item["id"] for item in unique] == [5, 6]


def test_table_lines_include_titles_from_report_row() -> None:
    report = {
        "archetypes": [
            {
                "archetype": "Neon Insomniac",
                "movie_count": 2,
                "game_count": 1,
                "rung_used": "a",
                "degraded": False,
                "unresolved_keywords": [],
                "top_movie_titles": ["Arrival", "Blade Runner"],
                "top_game_titles": ["Inside"],
                "latency_ms": {
                    "movie_ms": {"p50": 10.0, "max": 12.0},
                    "game_total_ms": {"p50": 10.0, "max": 12.0},
                    "game_api_ms": {"p50": 8.0, "max": 9.0},
                    "total_ms": {"p50": 12.0, "max": 14.0},
                },
            }
        ]
    }
    lines = _table_lines(report)
    assert "Neon Insomniac | 2 | 1 | a | False" in lines[1]
    assert "Arrival, Blade Runner" in lines[1]
    assert "Inside" in lines[1]


def test_connect_trace_callback_is_async_and_records_durations(tmp_path) -> None:
    class TraceClient:
        async def request(self, method, url, *, headers=None, extensions=None, **kwargs):
            trace = extensions["trace"]
            assert inspect.iscoroutinefunction(trace)
            await trace("connection.connect_tcp.started", {})
            await trace("connection.connect_tcp.complete", {})
            await trace("connection.start_tls.started", {})
            await trace("connection.start_tls.complete", {})
            return httpx.Response(200, json={})

    client = CatalogClients(
        tmdb_token="token",
        client=TraceClient(),
        token_path=tmp_path / "token.json",
    )
    result = asyncio.run(client.measure_connect_tls("tmdb"))
    assert set(result) == {"tcp_ms", "tls_ms", "connect_tls_ms"}
    assert abs(result["connect_tls_ms"] - result["tcp_ms"] - result["tls_ms"]) < 0.02


def test_connect_measurement_failure_is_returned_without_raising(tmp_path) -> None:
    class FailingClient:
        async def request(self, method, url, *, headers=None, extensions=None, **kwargs):
            raise RuntimeError("trace transport failed")

    client = CatalogClients(
        tmdb_token="token",
        client=FailingClient(),
        token_path=tmp_path / "token.json",
    )
    result = asyncio.run(client.measure_connect_tls("tmdb"))
    assert result == {"error": "RuntimeError: trace transport failed"}


def test_retry_backoff_schedule_and_retry_after() -> None:
    assert tmdb_backoff_delay(0, jitter=0) == 0.2
    assert tmdb_backoff_delay(1, jitter=0) == 0.6
    assert tmdb_backoff_delay(2, jitter=0) == 1.2
    assert tmdb_backoff_delay(0, retry_after="2.5", jitter=0) == 2.5


def test_tmdb_retries_5xx_and_records_attempt(monkeypatch, tmp_path) -> None:
    requests = 0
    delays: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        if requests < 3:
            return httpx.Response(503, json={"message": "temporary"})
        return httpx.Response(200, json={"genres": []})

    async def no_wait(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr("app.services.catalog.clients.asyncio.sleep", no_wait)
    monkeypatch.setattr("app.services.catalog.clients.random.uniform", lambda low, high: 0)
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    clients = CatalogClients(
        tmdb_token="token", client=http, cache=JsonDiskCache(tmp_path / "cache"),
        token_path=tmp_path / "token",
    )
    result = asyncio.run(clients.tmdb_genres())
    asyncio.run(http.aclose())
    assert result.ok
    assert result.attempt == 3
    assert requests == 3
    assert delays == [0.2, 0.6]


def test_tmdb_transport_error_detail_includes_exception_and_attempt(monkeypatch, tmp_path) -> None:
    requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        raise httpx.ConnectError("offline")

    async def no_wait(delay: float) -> None:
        return None

    monkeypatch.setattr("app.services.catalog.clients.asyncio.sleep", no_wait)
    monkeypatch.setattr("app.services.catalog.clients.random.uniform", lambda low, high: 0)
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    clients = CatalogClients(
        tmdb_token="token", client=http, cache=JsonDiskCache(tmp_path / "cache"),
        token_path=tmp_path / "token",
    )
    result = asyncio.run(clients.tmdb_keyword("keyword"))
    asyncio.run(http.aclose())
    assert requests == 4
    assert result.error_code == "transport_error"
    assert result.error_detail == "ConnectError: offline"
    assert result.attempt == 4


def test_tmdb_semaphore_bounds_concurrent_calls(tmp_path) -> None:
    class SlowClient:
        def __init__(self) -> None:
            self.active = 0
            self.maximum = 0

        async def request(self, method, url, **kwargs):
            self.active += 1
            self.maximum = max(self.maximum, self.active)
            await asyncio.sleep(0.001)
            self.active -= 1
            return httpx.Response(200, json={"results": []})

    fake = SlowClient()
    clients = CatalogClients(
        tmdb_token="token", client=fake, tmdb_concurrency=4,
        token_path=tmp_path / "token",
    )

    async def exercise() -> None:
        await asyncio.gather(*(
            clients.tmdb_discover({"page": page}, cold=True)
            for page in range(12)
        ))

    asyncio.run(exercise())
    assert fake.maximum == 4


def test_keyword_match_prefers_exact_then_highest_rank() -> None:
    results = [
        {"id": 1, "name": "near", "score": 0.98},
        {"id": 2, "name": "Exact Key", "score": 0.4},
    ]
    assert select_keyword_match(results, "exact key")["id"] == 2
    assert select_keyword_match(results[:1], "missing")["id"] == 1
    assert select_keyword_match([], "missing") is None


def test_keyword_empty_200_is_no_match(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": []})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    clients = CatalogClients(
        tmdb_token="token", client=http, cache=JsonDiskCache(tmp_path / "cache"),
        token_path=tmp_path / "token",
    )
    result = asyncio.run(clients.tmdb_keyword("not found"))
    asyncio.run(http.aclose())
    assert result.status == "no_match"
    assert result.error_code is None


def test_fallback_ladder_stops_at_first_rung_with_sixty_movies() -> None:
    class FixtureClient:
        def __init__(self) -> None:
            self.params: list[dict[str, str | int]] = []

        async def tmdb_discover(self, params, *, cold=False):
            self.params.append(params)
            if "primary_release_date.gte" in params:
                ids = range(30)
            elif "with_keywords" in params:
                ids = range(100, 140)
            else:
                ids = range(200, 260)
            return CatalogResult(
                "discover/movie", 200,
                {"results": [{"id": item, "title": f"Movie {item}"} for item in ids]},
            )

    intent = {
        "tmdb_genres": ["Drama"],
        "tmdb_keywords": ["classic"],
        "tmdb_date_range": ("1900-01-01", "2005-12-31"),
    }
    ids = {"tmdb_genres": {"drama": 18}, "tmdb_keywords": {"classic": 20}}
    client = FixtureClient()
    result = asyncio.run(_fetch_intent(client, intent, ids, True))
    assert result["rung_used"] == "c"
    assert len(result["items"]) == 60
    assert len(client.params) == 6


def test_degraded_flag_marks_unresolved_keyword_and_short_result() -> None:
    class FixtureClient:
        async def tmdb_discover(self, params, *, cold=False):
            return CatalogResult("discover/movie", 200, {"results": [{"id": 1}]})

    intent = {"tmdb_genres": ["Drama"], "tmdb_keywords": ["unmapped"]}
    ids = {"tmdb_genres": {"drama": 18}, "tmdb_keywords": {"unmapped": None}}
    result = asyncio.run(_fetch_intent(FixtureClient(), intent, ids, True))
    assert result["degraded"]
    assert result["unresolved_keywords"] == ["unmapped"]


def test_jaccard_reports_mean_max_and_flagged_pairs() -> None:
    profiles = [
        {"archetype": "a", "ids": [1, 2, 3]},
        {"archetype": "b", "ids": [1, 2, 3]},
        {"archetype": "c", "ids": [9]},
    ]
    report = _jaccard_report(profiles, "ids")
    assert report["max"] == 1.0
    assert report["mean"] > 0
    assert report["flagged_pairs"][0]["overlap_flag"]


def test_multiquery_body_contains_all_labeled_queries() -> None:
    bodies = [
        ("genres", build_apicalypse([1], [], "genres")),
        ("themes", build_apicalypse([], [2], "themes")),
        ("combined", build_apicalypse([1], [2], "combined")),
    ]
    body = build_multiquery(bodies)
    assert body.count("query games") == 3
    assert 'query games "genres"' in body
    assert 'query games "themes"' in body
    assert 'query games "combined"' in body


def test_token_bucket_capacity_and_refill(monkeypatch) -> None:
    now = [0.0]
    waits: list[float] = []

    async def sleep(delay: float) -> None:
        waits.append(delay)
        now[0] += delay

    monkeypatch.setattr("app.services.catalog.clients.time.monotonic", lambda: now[0])
    monkeypatch.setattr("app.services.catalog.clients.asyncio.sleep", sleep)

    async def exercise() -> None:
        bucket = AsyncTokenBucket(4, 4)
        for _ in range(5):
            await bucket.acquire()

    asyncio.run(exercise())
    assert waits == [0.25]


def test_igdb_semaphore_caps_open_requests_and_records_separate_timings(tmp_path) -> None:
    class SlowClient:
        def __init__(self) -> None:
            self.active = 0
            self.maximum = 0

        async def request(self, method, url, **kwargs):
            self.active += 1
            self.maximum = max(self.maximum, self.active)
            await asyncio.sleep(0.001)
            self.active -= 1
            return httpx.Response(200, json=[])

    class WaitingBucket:
        async def acquire(self) -> float:
            await asyncio.sleep(0.001)
            return 1.0

    fake = SlowClient()
    clients = CatalogClients(
        client=fake,
        twitch_client_id="client",
        igdb_bucket=WaitingBucket(),
        igdb_concurrency=8,
        token_path=tmp_path / "token",
    )
    clients._token = "fixture-token"
    clients._token_expiry = 10_000_000_000

    async def exercise() -> list[CatalogResult]:
        return await asyncio.gather(*(
            clients.igdb_games("fields id; limit 1;", cold=True)
            for _ in range(12)
        ))

    results = asyncio.run(exercise())
    assert fake.maximum == 8
    assert all(result.limiter_wait_ms > 0 for result in results)
    assert all(result.api_ms > 0 for result in results)
