"""Offline ranking tests for top-k output and genre diversification."""

from __future__ import annotations

import asyncio
import re

from app.services.catalog.clients import CatalogResult, JsonDiskCache
from app.services.ranking import DIM_PHRASES, RankingService, _explanation, _mmr
from app.services.vibe.archetypes import DIMENSIONS, POPULATION_MEAN, POPULATION_STD


class FakeCatalog:
    async def tmdb_configuration(self) -> CatalogResult:
        return CatalogResult(
            "configuration", 200,
            {"images": {"secure_base_url": "https://img.test/"}},
        )


def test_ranking_returns_ten_and_diversifies_genres(tmp_path) -> None:
    service = RankingService(FakeCatalog(), ttl_seconds=60)
    movies = [
        {
            "id": index,
            "title": f"Film {index}",
            "release_date": "2020-01-01",
            "poster_path": f"/{index}.jpg",
            "vote_average": 8.0,
            "genre_ids": [1 if index < 8 else 2],
        }
        for index in range(15)
    ]
    games = [
        {
            "id": index,
            "name": f"Game {index}",
            "first_release_date": 1_600_000_000,
            "total_rating": 80,
            "genres": [1 if index < 8 else 2],
            "themes": [],
            "cover": {"image_id": f"cover{index}"},
        }
        for index in range(15)
    ]
    primary = {"movies": movies, "games": games, "degraded": False}
    secondary = {"movies": [], "games": [], "degraded": False}
    ids = {
        "tmdb_genres": [
            {"id": 1, "name": "Action"},
            {"id": 2, "name": "Documentary"},
        ],
        "igdb_genres": [
            {"id": 1, "name": "Action"},
            {"id": 2, "name": "Puzzle"},
        ],
        "igdb_themes": [],
    }

    async def pools_and_ids(primary_name: str, secondary_name: str):
        return primary, secondary, ids

    service._pools_and_ids = pools_and_ids
    vector = {
        "energy": 0.8,
        "valence": 0.7,
        "acousticness": 0.2,
        "danceability": 0.8,
        "instrumentalness": 0.1,
        "tempo": 0.8,
        "era": 0.7,
        "mainstream": 0.7,
    }
    results = asyncio.run(service.rank(vector, "Neon Insomniac", "Solar Sprinter"))
    assert len(results["movies"]) == 10
    assert len(results["games"]) == 10
    assert len({item["id"] for item in results["movies"]}) == 10
    assert all(60 <= item["match_pct"] <= 98 for item in results["movies"])
    assert all(item["why"].endswith(".") for item in results["movies"])
    assert all("late-night, driving" not in item["why"] for item in results["movies"])
    assert results["games"][0]["image_url"].startswith(
        "https://images.igdb.com/igdb/image/upload/t_cover_big/"
    )


def test_why_copy_is_grammatical_and_uses_distinct_allowed_phrases() -> None:
    vector = {
        "energy": 0.8, "valence": 0.7, "acousticness": 0.2, "danceability": 0.8,
        "instrumentalness": 0.1, "tempo": 0.8, "era": 0.7, "mainstream": 0.7,
    }
    allowed = {phrase for options in DIM_PHRASES.values() for phrase in options}
    for index in range(32):
        why = _explanation(vector, vector, f"fixture-title-{index}")
        assert why[0].isupper()
        assert why.endswith(".")
        words = re.findall(r"[a-z]+", why.casefold())
        pairs = list(zip(words, words[1:], strict=False))
        assert len(pairs) == len(set(pairs))
        assert sum(phrase in why for phrase in allowed) == 2


def test_why_copy_has_one_dimension_and_no_dimension_fallbacks() -> None:
    vector = dict(zip(DIMENSIONS, POPULATION_MEAN, strict=True))
    one_dim = dict(vector)
    one_dim["energy"] += POPULATION_STD[0] * 0.5
    only_energy = _explanation(one_dim, one_dim, "one-dimension")
    assert only_energy[0].isupper() and only_energy.endswith(".")
    assert "high-energy" in only_energy
    no_dims = _explanation(vector, vector, "no-dimensions")
    assert no_dims == "Close to the sound of your top genres."


def test_mmr_minmax_normalizes_match_percentages() -> None:
    picks = _mmr([
        {"id": "a", "score": 0.4, "_genres": {"a"}},
        {"id": "b", "score": 0.7, "_genres": {"b"}},
    ])
    assert sorted(pick["match_pct"] for pick in picks) == [72, 97]


def test_catalog_failure_returns_the_available_provider_only() -> None:
    service = RankingService(FakeCatalog())
    game = {
        "id": 99, "name": "Game", "total_rating": 80,
        "genres": [1], "themes": [],
    }
    failed_movie_pool = {
        "movies": [], "games": [game], "movies_failed": True,
        "games_failed": False, "degraded": True,
    }
    healthy_game_pool = {
        "movies": [], "games": [game], "movies_failed": True,
        "games_failed": False, "degraded": True,
    }
    ids = {
        "tmdb_genres": [], "igdb_genres": [{"id": 1, "name": "Action"}],
        "igdb_themes": [], "boot_results": [],
    }

    async def pools_and_ids(primary_name, secondary_name):
        return failed_movie_pool, healthy_game_pool, ids

    service._pools_and_ids = pools_and_ids
    vector = dict(zip(DIMENSIONS, POPULATION_MEAN, strict=True))
    result = asyncio.run(service.rank(vector, "Neon Insomniac", "Solar Sprinter"))
    assert result["movies"] == []
    assert len(result["games"]) == 1
    assert result["degraded"] is True
    assert result["upstream_error"] is False
    assert "movies unavailable" in result["degraded_reasons"]


def test_both_catalog_failures_are_marked_as_upstream_error() -> None:
    service = RankingService(FakeCatalog())
    pool = {
        "movies": [], "games": [], "movies_failed": True,
        "games_failed": True, "degraded": True,
    }
    ids = {"tmdb_genres": [], "igdb_genres": [], "igdb_themes": [],
           "boot_results": []}

    async def pools_and_ids(primary_name, secondary_name):
        return pool, pool, ids

    service._pools_and_ids = pools_and_ids
    vector = dict(zip(DIMENSIONS, POPULATION_MEAN, strict=True))
    result = asyncio.run(service.rank(vector, "Neon Insomniac", "Solar Sprinter"))
    assert result["upstream_error"] is True
    assert result["movies"] == []
    assert result["games"] == []


def test_regional_pool_uses_language_or_filters_and_marks_candidates(tmp_path) -> None:
    class RegionalCatalog(FakeCatalog):
        def __init__(self):
            self.queries = []

        async def tmdb_discover(self, params, *, cold=False):
            self.queries.append(params)
            index = params["page"]
            return CatalogResult("discover/movie", 200, {"results": [{
                "id": 100 + index, "title": f"Regional {index}",
                "vote_average": 8, "genre_ids": [1], "release_date": "2020-01-01",
            }]})

    catalog = RegionalCatalog()
    service = RankingService(catalog, disk_cache=JsonDiskCache(tmp_path / "cache", ttl=86400))
    primary = {"movies": [], "games": [], "degraded": False}
    secondary = {"movies": [], "games": [], "degraded": False}
    ids = {
        "tmdb_genres": [{"id": 1, "name": "Action"}],
        "igdb_genres": [], "igdb_themes": [],
    }

    async def pools_and_ids(primary_name, secondary_name):
        return primary, secondary, ids

    service._pools_and_ids = pools_and_ids
    vector = dict(zip(DIMENSIONS, POPULATION_MEAN, strict=True))
    families = [{"id": "hindi-film", "share": 0.25}]
    ranked = asyncio.run(service.rank(vector, "Neon Insomniac", "Solar Sprinter",
                                      top_families=families))
    assert len(catalog.queries) == 2
    assert all(query["with_original_language"] == "hi|pa|ta|te" for query in catalog.queries)
    assert all(query["vote_count.gte"] == 500 for query in catalog.queries)
    assert all(query["vote_average.gte"] == 6 for query in catalog.queries)
    assert all(pick["regional"] for pick in ranked["movies"])


def test_regional_pool_serves_stale_movies_after_retrieval_failure(caplog) -> None:
    class FailingCatalog(FakeCatalog):
        async def tmdb_discover(self, params, *, cold=False):
            return CatalogResult("discover/movie", None, None, "transport_error",
                                 "offline", "ConnectError: offline")

    class StaleCache:
        def get(self, key):
            return None

        def get_stale(self, key):
            return [{"id": 3, "title": "Cached regional"}]

        def set(self, key, value):
            raise AssertionError("failed regional result must not replace stale data")

    service = RankingService(FailingCatalog(), disk_cache=StaleCache())
    result = asyncio.run(service._regional_pool())
    assert result[0][0]["id"] == 3
    assert result[1:] == (True, False)
    assert "serving stale catalog pool" in caplog.text


def test_mmr_prefers_a_different_genre_after_first_pick() -> None:
    candidates = [
        {"id": "a1", "score": 0.8, "_genres": {"action"}},
        {"id": "a2", "score": 0.8, "_genres": {"action"}},
        {"id": "d1", "score": 0.8, "_genres": {"documentary"}},
    ]
    result = _mmr(candidates, limit=2)
    assert result[0]["id"] == "a1"
    assert result[1]["id"] == "d1"
