"""Offline request-shape tests for the prototype HTTP API."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app, get_lastfm_client, get_ranking_service
from app.services.catalog.clients import ConfigError
from spike.lastfm_client import LastFmResult


class FakeLastFm:
    def __init__(self, artists: int = 6) -> None:
        self.artists = artists

    def user_top_artists(self, username: str, period: str, limit: int = 50) -> LastFmResult:
        entries = [
            {"name": f"Artist {index}", "playcount": str(index + 1)}
            for index in range(self.artists)
        ]
        return LastFmResult(
            "user.getTopArtists", 200, {"topartists": {"artist": entries}}
        )

    def artist_top_tags(self, artist: str) -> LastFmResult:
        tags = [
            {"name": "hip-hop", "count": "80"},
            {"name": "rap", "count": "60"},
            {"name": "boom bap", "count": "40"},
        ]
        return LastFmResult("artist.getTopTags", 200, {"toptags": {"tag": tags}})

    def artist_get_info(self, artist: str) -> LastFmResult:
        return LastFmResult(
            "artist.getInfo", 200,
            {"artist": {"stats": {"listeners": "100000"}}},
        )

    def artist_similar(self, artist: str, limit: int = 5) -> LastFmResult:
        return LastFmResult("artist.getSimilar", 200, {"similarartists": {"artist": []}})


class FakeRanker:
    async def rank(self, vector, primary, secondary, *, top_families=None):
        return {
            "movies": [
                {
                    "id": str(index), "title": f"Movie {index}", "year": "2020",
                    "image_url": None, "score": 0.8, "match_pct": 90,
                    "why": "Matches your taste.",
                    "source_url": "https://example.test/movie",
                }
                for index in range(10)
            ],
            "games": [
                {
                    "id": str(index), "title": f"Game {index}", "year": "2020",
                    "image_url": None, "score": 0.8, "match_pct": 90,
                    "why": "Matches your taste.",
                    "source_url": "https://example.test/game",
                }
                for index in range(10)
            ],
            "degraded": False,
        }


def _client(lastfm: FakeLastFm | None = None) -> TestClient:
    app.dependency_overrides[get_lastfm_client] = lambda: lastfm or FakeLastFm()
    app.dependency_overrides[get_ranking_service] = lambda: FakeRanker()
    return TestClient(app)


def test_seed_analyze_returns_response_shape() -> None:
    with _client() as client:
        response = client.post(
            "/analyze", json={"mode": "seed", "artists": ["A", "B", "C"]}
        )
    app.dependency_overrides.clear()
    assert response.status_code == 200
    data = response.json()
    assert set(data["vector"]) == {
        "energy", "valence", "acousticness", "danceability",
        "instrumentalness", "tempo", "era", "mainstream",
    }
    assert len(data["movies"]) == 10
    assert len(data["games"]) == 10
    assert all("match_pct" in pick for pick in data["movies"] + data["games"])
    assert data["signal_pct"] >= 0
    assert "attribution" in data
    assert "degraded_reasons" in data


def test_lastfm_analyze_returns_response_shape() -> None:
    with _client() as client:
        response = client.post("/analyze", json={"mode": "lastfm", "username": "listener"})
    app.dependency_overrides.clear()
    assert response.status_code == 200
    data = response.json()
    assert data["archetype"]
    assert data["secondary"]
    assert len(data["top_families"]) <= 3


def test_lastfm_analyze_requires_five_artists() -> None:
    with _client(FakeLastFm(artists=4)) as client:
        response = client.post("/analyze", json={"mode": "lastfm", "username": "listener"})
    app.dependency_overrides.clear()
    assert response.status_code == 422
    assert "at least five" in response.json()["detail"]


def test_config_error_returns_503_with_clear_message() -> None:
    def fail_config() -> FakeLastFm:
        raise ConfigError("TMDB_READ_TOKEN contains non-ASCII characters; re-copy the full token")

    app.dependency_overrides[get_lastfm_client] = fail_config
    app.dependency_overrides[get_ranking_service] = lambda: FakeRanker()
    with TestClient(app) as client:
        response = client.post(
            "/analyze", json={"mode": "seed", "artists": ["A", "B", "C"]}
        )
    app.dependency_overrides.clear()
    assert response.status_code == 503
    assert "re-copy the full token" in response.json()["detail"]


def test_catalog_failures_return_partial_results_without_502() -> None:
    class PartialRanker(FakeRanker):
        async def rank(self, vector, primary, secondary, *, top_families=None):
            return {
                "movies": [], "games": [{"id": "g1", "title": "Game"}],
                "degraded": True,
                "degraded_reasons": ["movies unavailable"],
            }

    app.dependency_overrides[get_lastfm_client] = lambda: FakeLastFm()
    app.dependency_overrides[get_ranking_service] = lambda: PartialRanker()
    with TestClient(app) as client:
        response = client.post(
            "/analyze", json={"mode": "seed", "artists": ["A", "B", "C"]}
        )
    app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json()["movies"] == []
    assert response.json()["games"][0]["id"] == "g1"
    assert response.json()["degraded"] is True


def test_both_catalog_failures_return_provider_and_reason(caplog) -> None:
    class FailedRanker(FakeRanker):
        async def rank(self, vector, primary, secondary, *, top_families=None):
            return {
                "movies": [], "games": [], "degraded": True,
                "upstream_error": True,
                "failed_providers": ["TMDB", "IGDB"],
            }

    app.dependency_overrides[get_lastfm_client] = lambda: FakeLastFm()
    app.dependency_overrides[get_ranking_service] = lambda: FailedRanker()
    with TestClient(app) as client:
        response = client.post(
            "/analyze", json={"mode": "seed", "artists": ["A", "B", "C"]}
        )
    app.dependency_overrides.clear()
    assert response.status_code == 502
    assert response.json() == {
        "provider": "TMDB/IGDB", "reason": "Catalog results are unavailable."
    }
    assert "provider=TMDB/IGDB" in caplog.text
    assert "endpoint=catalog retrieval" in caplog.text
