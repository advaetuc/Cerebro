"""Offline ranking tests for top-k output and genre diversification."""

from __future__ import annotations

import asyncio

from app.services.catalog.clients import CatalogResult
from app.services.ranking import RankingService, _mmr


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
    assert results["games"][0]["image_url"].startswith(
        "https://images.igdb.com/igdb/image/upload/t_cover_big/"
    )


def test_mmr_prefers_a_different_genre_after_first_pick() -> None:
    candidates = [
        {"id": "a1", "score": 0.8, "_genres": {"action"}},
        {"id": "a2", "score": 0.8, "_genres": {"action"}},
        {"id": "d1", "score": 0.8, "_genres": {"documentary"}},
    ]
    result = _mmr(candidates, limit=2)
    assert result[0]["id"] == "a1"
    assert result[1]["id"] == "d1"
