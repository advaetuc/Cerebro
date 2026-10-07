"""Small synchronous Last.fm API client for the Phase 0 spike."""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

API_URL = "https://ws.audioscrobbler.com/2.0/"
CACHE_DIR = Path(__file__).resolve().parent / ".cache"


@dataclass(frozen=True)
class LastFmResult:
    """Represent an HTTP or Last.fm API result without raising API errors."""

    endpoint: str
    status: int | None
    data: dict[str, Any]
    error_code: int | None = None
    error_message: str | None = None
    query_param_names: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        """Return whether the call succeeded at both HTTP and API levels."""
        return self.status is not None and self.status < 400 and self.error_code is None


class RateLimiter:
    """Space calls to enforce a maximum request rate."""

    def __init__(self, max_requests_per_second: float = 4.0) -> None:
        if max_requests_per_second <= 0:
            raise ValueError("Request rate must be positive")
        self.interval = 1.0 / max_requests_per_second
        self._last_request: float | None = None

    def wait(self) -> None:
        """Wait until the next request meets the configured spacing."""
        now = time.monotonic()
        if self._last_request is not None:
            delay = self.interval - (now - self._last_request)
            if delay > 0:
                time.sleep(delay)
        self._last_request = time.monotonic()


class LastFmClient:
    """Call the Last.fm JSON API with rate limiting and error-code retries."""

    def __init__(
        self,
        api_key: str | None = None,
        limiter: RateLimiter | None = None,
        transport: httpx.BaseTransport | None = None,
        max_retries: int = 3,
        cache_dir: Path | None = None,
    ) -> None:
        load_dotenv()
        self.api_key = api_key or os.getenv("LASTFM_API_KEY", "").strip()
        if not self.api_key:
            raise ValueError("Set LASTFM_API_KEY in services/api/.env")
        self.limiter = limiter or RateLimiter()
        self.client = httpx.Client(transport=transport, timeout=20)
        self.max_retries = max_retries
        self.cache_dir = cache_dir or CACHE_DIR

    def _cache_path(self, method: str, params: dict[str, str | int]) -> Path:
        cache_key = json.dumps(
            {"method": method, "params": sorted(params.items())},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        digest = hashlib.sha256(cache_key.encode("utf-8")).hexdigest()
        return self.cache_dir / f"{digest}.json"

    def _cached_result(self, method: str, params: dict[str, str | int]) -> LastFmResult | None:
        path = self._cache_path(method, params)
        try:
            cached = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return LastFmResult(
            endpoint=method,
            status=cached.get("status"),
            data=cached.get("data", {}),
            error_code=cached.get("error_code"),
            error_message=cached.get("error_message"),
            query_param_names=tuple(cached.get("query_param_names", ())),
        )

    def _save_result(
        self,
        method: str,
        params: dict[str, str | int],
        result: LastFmResult,
    ) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        path = self._cache_path(method, params)
        path.write_text(
            json.dumps(
                {
                    "status": result.status,
                    "data": result.data,
                    "error_code": result.error_code,
                    "error_message": result.error_message,
                    "query_param_names": result.query_param_names,
                }
            ),
            encoding="utf-8",
        )

    def close(self) -> None:
        """Close the underlying HTTP client."""
        self.client.close()

    def _call(self, method: str, **params: str | int) -> LastFmResult:
        cached = self._cached_result(method, params)
        if cached is not None:
            return cached
        query_params = {"method": method, "api_key": self.api_key, "format": "json", **params}
        for attempt in range(self.max_retries + 1):
            self.limiter.wait()
            try:
                response = self.client.get(
                    API_URL,
                    params=query_params,
                )
            except httpx.HTTPError:
                if attempt == self.max_retries:
                    return LastFmResult(
                        method,
                        None,
                        {},
                        error_message="HTTP request failed",
                        query_param_names=tuple(query_params),
                    )
                time.sleep(min(2**attempt * 0.25, 4.0))
                continue
            try:
                payload = response.json()
            except ValueError:
                payload = {}
            if not isinstance(payload, dict):
                payload = {}
            error_code = payload.get("error")
            if isinstance(error_code, int) and error_code == 29 and attempt < self.max_retries:
                time.sleep(min(2**attempt * 0.25, 4.0))
                continue
            if response.status_code == 429 and attempt < self.max_retries:
                retry_after = response.headers.get("Retry-After")
                try:
                    delay = float(retry_after) if retry_after else min(2**attempt * 0.25, 4.0)
                except ValueError:
                    delay = min(2**attempt * 0.25, 4.0)
                time.sleep(max(0.0, delay))
                continue
            result = LastFmResult(
                endpoint=method,
                status=response.status_code,
                data=payload,
                error_code=error_code if isinstance(error_code, int) else None,
                error_message=str(payload.get("message", "")) or None,
                query_param_names=tuple(query_params),
            )
            self._save_result(method, params, result)
            return result
        return LastFmResult(
            method,
            None,
            {},
            error_message="Request retry limit reached",
            query_param_names=tuple(query_params),
        )

    def user_get_info(self, username: str) -> LastFmResult:
        """Get public account information for a Last.fm username."""
        return self._call("user.getInfo", user=username)

    def user_top_artists(self, username: str, period: str, limit: int = 50) -> LastFmResult:
        """Get a user's top artists for a Last.fm period."""
        return self._call("user.getTopArtists", user=username, period=period, limit=limit)

    def user_top_tracks(self, username: str, period: str, limit: int = 50) -> LastFmResult:
        """Get a user's top tracks for a Last.fm period."""
        return self._call("user.getTopTracks", user=username, period=period, limit=limit)

    def user_recent_tracks(self, username: str, limit: int = 50) -> LastFmResult:
        """Get a user's recent tracks."""
        return self._call("user.getRecentTracks", user=username, limit=limit)

    def artist_top_tags(self, artist: str) -> LastFmResult:
        """Get tags for an artist."""
        return self._call("artist.getTopTags", artist=artist)

    def artist_get_info(self, artist: str) -> LastFmResult:
        """Get public information for an artist."""
        return self._call("artist.getInfo", artist=artist)

    def artist_similar(self, artist: str, limit: int = 5) -> LastFmResult:
        """Get similar artists for an artist."""
        return self._call("artist.getSimilar", artist=artist, limit=limit)

    def artist_search(self, query: str, limit: int = 10) -> LastFmResult:
        """Search for artists by name."""
        return self._call("artist.search", artist=query, limit=limit)
