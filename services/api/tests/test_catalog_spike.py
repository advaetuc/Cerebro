"""Offline tests for catalog query construction and caching helpers."""

import asyncio
import inspect

from spike.catalog_clients import (
    AsyncRateLimiter,
    CatalogClients,
    CatalogResult,
    build_apicalypse,
    build_tmdb_params,
    token_is_valid,
)
from spike.probe_catalog import (
    INTENTS,
    _dedupe,
    _igdb_queries,
    _table_lines,
    _tmdb_queries,
)


def test_intent_names_resolve_to_runtime_query_ids() -> None:
    intent = INTENTS["Neon Insomniac"]
    ids = {
        "tmdb_genres": {"science fiction": 878, "thriller": 53},
        "tmdb_keywords": {name: index for index, name in enumerate(intent["tmdb_keywords"], 10)},
        "igdb_genres": {
            name.casefold(): index
            for index, name in enumerate(intent["igdb_genres"], 20)
        },
        "igdb_themes": {
            name.casefold(): index
            for index, name in enumerate(intent["igdb_themes"], 30)
        },
    }
    movie_queries = _tmdb_queries(intent, ids)
    game_queries = _igdb_queries(intent, ids)
    assert len(movie_queries) == 6
    assert len(game_queries) == 3
    assert all("with_genres" in query and "with_keywords" in query for query in movie_queries)
    assert all("total_rating_count >= 20" in query for query in game_queries)


def test_tmdb_query_builder_uses_resolved_ids_and_vote_floor() -> None:
    query = build_tmdb_params([878], [42])
    assert query["with_genres"] == "878"
    assert query["with_keywords"] == "42"
    assert query["vote_count.gte"] == 500


def test_apicalypse_body_is_stable() -> None:
    assert build_apicalypse([12], [31]) == (
        "fields id,name,total_rating_count,rating; where total_rating_count >= 20 & "
        "genres = (12) & themes = (31); limit 50;"
    )


def test_token_expiry_reserves_sixty_seconds() -> None:
    assert token_is_valid(1_061, now=1_000)
    assert not token_is_valid(1_060, now=1_000)
    assert not token_is_valid(999, now=1_000)


def test_async_limiter_spaces_requests(monkeypatch) -> None:
    now = [0.0]
    waits: list[float] = []

    async def sleep(delay: float) -> None:
        waits.append(delay)
        now[0] += delay

    monkeypatch.setattr("spike.catalog_clients.time.monotonic", lambda: now[0])
    monkeypatch.setattr("spike.catalog_clients.asyncio.sleep", sleep)

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
                "latency_ms": {
                    mode: {
                        metric: {"p50": 10.0, "max": 12.0}
                        for metric in ("movie_ms", "game_ms", "total_ms")
                    }
                    for mode in ("cold", "warm")
                },
                "top_movie_titles": ["Arrival", "Blade Runner"],
                "top_game_titles": ["Inside"],
            }
        ]
    }
    lines = _table_lines(report)
    assert "Arrival, Blade Runner" in lines[1]
    assert "Inside" in lines[1]


def test_connect_trace_callback_is_async_and_records_durations(tmp_path) -> None:
    class TraceClient:
        async def get(self, url, *, headers=None, extensions=None):
            trace = extensions["trace"]
            assert inspect.iscoroutinefunction(trace)
            await trace("connection.connect_tcp.started", {})
            await trace("connection.connect_tcp.complete", {})
            await trace("connection.start_tls.started", {})
            await trace("connection.start_tls.complete", {})

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
        async def get(self, url, *, headers=None, extensions=None):
            raise RuntimeError("trace transport failed")

    client = CatalogClients(
        tmdb_token="token",
        client=FailingClient(),
        token_path=tmp_path / "token.json",
    )
    result = asyncio.run(client.measure_connect_tls("tmdb"))
    assert result == {"error": "RuntimeError: trace transport failed"}
