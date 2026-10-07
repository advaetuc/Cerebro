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
    async def rank(self, vector, primary, secondary):
        return {
            "movies": [
                {
                    "id": str(index), "title": f"Movie {index}", "year": "2020",
                    "image_url": None, "score": 0.8, "why": "Matches your taste.",
                    "source_url": "https://example.test/movie",
                }
                for index in range(10)
            ],
            "games": [
                {
                    "id": str(index), "title": f"Game {index}", "year": "2020",
                    "image_url": None, "score": 0.8, "why": "Matches your taste.",
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
    assert data["signal_pct"] >= 0
    assert "attribution" in data


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
