"""Async TMDB and IGDB clients for the catalog retrieval spike."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

TMDB_BASE = "https://api.themoviedb.org/3"
TWITCH_TOKEN_URL = "https://id.twitch.tv/oauth2/token"
IGDB_BASE = "https://api.igdb.com/v4"
CACHE_DIR = Path(__file__).resolve().parent / ".cache" / "catalog"
CACHE_TTL_SECONDS = 7 * 24 * 60 * 60


def token_is_valid(expires_at: float, now: float | None = None) -> bool:
    """Return whether a token remains valid beyond the 60-second safety margin."""
    return (time.time() if now is None else now) < expires_at - 60


@dataclass(frozen=True)
class CatalogResult:
    """Represent successful data or an HTTP/API failure without raising it."""

    endpoint: str
    status: int | None
    data: Any
    error_code: str | int | None = None
    error_message: str | None = None
    latency_ms: float = 0.0

    @property
    def ok(self) -> bool:
        """Return whether the response has no transport or API error."""
        return self.status is not None and self.status < 400 and self.error_code is None


class AsyncRateLimiter:
    """Space asynchronous calls to stay within a maximum request rate."""

    def __init__(self, max_requests_per_second: float = 4.0) -> None:
        if max_requests_per_second <= 0:
            raise ValueError("Request rate must be positive")
        self.interval = 1.0 / max_requests_per_second
        self._last_request: float | None = None
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        """Wait for the next allowed request slot."""
        async with self._lock:
            now = time.monotonic()
            if self._last_request is not None:
                delay = self.interval - (now - self._last_request)
                if delay > 0:
                    await asyncio.sleep(delay)
            self._last_request = time.monotonic()


class JsonDiskCache:
    """Cache catalog ID lists with a bounded TTL."""

    def __init__(self, directory: Path = CACHE_DIR, ttl: int = CACHE_TTL_SECONDS) -> None:
        self.directory = directory
        self.ttl = ttl

    def _path(self, key: str) -> Path:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return self.directory / f"{digest}.json"

    def get(self, key: str) -> Any | None:
        """Return an unexpired cached value, if present."""
        try:
            payload = json.loads(self._path(key).read_text(encoding="utf-8"))
            if time.time() - float(payload["saved_at"]) > self.ttl:
                return None
            return payload["value"]
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def set(self, key: str, value: Any) -> None:
        """Persist an ID-list value with its creation time."""
        self.directory.mkdir(parents=True, exist_ok=True)
        self._path(key).write_text(
            json.dumps({"saved_at": time.time(), "value": value}), encoding="utf-8"
        )


def _api_error(response: httpx.Response, payload: Any) -> tuple[str | int | None, str | None]:
    if response.status_code >= 400:
        body = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
        return response.status_code, body[:200]
    if isinstance(payload, dict) and payload.get("error"):
        error = payload["error"]
        code = error.get("code", "api_error") if isinstance(error, dict) else "api_error"
        message = error.get("message", str(error)) if isinstance(error, dict) else str(error)
        return code, str(message)[:200]
    if isinstance(payload, dict) and payload.get("status_code"):
        return payload["status_code"], str(payload.get("status_message", payload))[:200]
    if isinstance(payload, list) and payload and isinstance(payload[0], dict):
        first = payload[0]
        if first.get("status") == "error" or first.get("error"):
            return first.get("error", "api_error"), str(first.get("message", first))[:200]
    return None, None


def _decode(response: httpx.Response, endpoint: str, started: float) -> CatalogResult:
    try:
        payload: Any = response.json()
    except ValueError:
        payload = response.text
    error_code, error_message = _api_error(response, payload)
    return CatalogResult(
        endpoint=endpoint,
        status=response.status_code,
        data=payload,
        error_code=error_code,
        error_message=error_message,
        latency_ms=(time.perf_counter() - started) * 1000,
    )


class CatalogClients:
    """Share one asynchronous HTTP client across TMDB and IGDB calls."""

    def __init__(
        self,
        tmdb_token: str | None = None,
        twitch_client_id: str | None = None,
        twitch_client_secret: str | None = None,
        *,
        client: httpx.AsyncClient | None = None,
        cache: JsonDiskCache | None = None,
        limiter: AsyncRateLimiter | None = None,
        token_path: Path | None = None,
    ) -> None:
        load_dotenv()
        self.tmdb_token = tmdb_token or os.getenv("TMDB_READ_TOKEN", "").strip()
        self.client_id = twitch_client_id or os.getenv("TWITCH_CLIENT_ID", "").strip()
        self.client_secret = twitch_client_secret or os.getenv("TWITCH_CLIENT_SECRET", "").strip()
        self.client = client or httpx.AsyncClient(timeout=20.0)
        self._owns_client = client is None
        self.cache = cache or JsonDiskCache()
        self.limiter = limiter or AsyncRateLimiter(4.0)
        self.token_path = token_path or CACHE_DIR / "igdb_token.json"
        self._token: str | None = None
        self._token_expiry = 0.0
        self._result_cache: dict[str, CatalogResult] = {}

    async def close(self) -> None:
        """Close the shared HTTP client when this instance owns it."""
        if self._owns_client:
            await self.client.aclose()

    def _cached_ids(self, key: str) -> Any | None:
        return self.cache.get(key)

    async def _request(
        self,
        method: str,
        url: str,
        endpoint: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, str | int] | None = None,
        content: str | None = None,
        data: dict[str, str] | None = None,
        igdb: bool = False,
        extensions: dict[str, Any] | None = None,
    ) -> CatalogResult:
        started = time.perf_counter()
        if igdb:
            await self.limiter.wait()
        try:
            response = await self.client.request(
                method,
                url,
                headers=headers,
                params=params,
                content=content,
                data=data,
                extensions=extensions,
            )
        except httpx.HTTPError as exc:
            return CatalogResult(
                endpoint,
                None,
                None,
                "transport_error",
                str(exc)[:200],
                (time.perf_counter() - started) * 1000,
            )
        return _decode(response, endpoint, started)

    async def tmdb_genres(self) -> CatalogResult:
        """Fetch the movie genre ID list, using the seven-day ID cache."""
        key = "tmdb:genre/movie/list"
        cached = self._cached_ids(key)
        if cached is not None:
            return CatalogResult(key, 200, {"genres": cached})
        if not self.tmdb_token:
            return CatalogResult(key, None, None, "missing_credentials", "TMDB_READ_TOKEN is unset")
        result = await self._request(
            "GET", f"{TMDB_BASE}/genre/movie/list", key,
            headers={"Authorization": f"Bearer {self.tmdb_token}"},
            params={"language": "en-US"},
        )
        if result.ok and isinstance(result.data, dict):
            self.cache.set(key, result.data.get("genres", []))
        return result

    async def tmdb_keyword(self, name: str) -> CatalogResult:
        """Resolve a keyword name to IDs, using the seven-day ID cache."""
        key = f"tmdb:keyword:{name.casefold()}"
        cached = self._cached_ids(key)
        if cached is not None:
            return CatalogResult(key, 200, {"results": cached})
        if not self.tmdb_token:
            return CatalogResult(key, None, None, "missing_credentials", "TMDB_READ_TOKEN is unset")
        result = await self._request(
            "GET", f"{TMDB_BASE}/search/keyword", key,
            headers={"Authorization": f"Bearer {self.tmdb_token}"},
            params={"query": name, "page": 1},
        )
        if result.ok and isinstance(result.data, dict):
            self.cache.set(key, result.data.get("results", []))
        return result

    async def tmdb_discover(
        self, params: dict[str, str | int], *, cold: bool = False
    ) -> CatalogResult:
        """Discover movies; cold calls bypass the in-memory result cache."""
        key = "tmdb:discover:" + json.dumps(params, sort_keys=True, separators=(",", ":"))
        if not cold and key in self._result_cache:
            return self._result_cache[key]
        if not self.tmdb_token:
            return CatalogResult(
                "discover/movie", None, None, "missing_credentials",
                "TMDB_READ_TOKEN is unset",
            )
        result = await self._request(
            "GET", f"{TMDB_BASE}/discover/movie", "discover/movie",
            headers={"Authorization": f"Bearer {self.tmdb_token}"}, params=params,
        )
        if not cold:
            self._result_cache[key] = result
        return result

    async def igdb_token(self) -> CatalogResult:
        """Load or obtain the Twitch client-credentials token."""
        now = time.time()
        if self._token and token_is_valid(self._token_expiry, now):
            return CatalogResult("oauth/token", 200, {"access_token": self._token})
        try:
            saved = json.loads(self.token_path.read_text(encoding="utf-8"))
            if token_is_valid(float(saved["expires_at"]), now):
                self._token = str(saved["access_token"])
                self._token_expiry = float(saved["expires_at"])
                return CatalogResult("oauth/token", 200, {"access_token": self._token})
        except (OSError, ValueError, KeyError, TypeError):
            pass
        if not self.client_id or not self.client_secret:
            return CatalogResult(
                "oauth/token", None, None, "missing_credentials",
                "Twitch credentials are unset",
            )
        result = await self._request(
            "POST", TWITCH_TOKEN_URL, "oauth/token",
            data={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "grant_type": "client_credentials",
            },
        )
        if not result.ok or not isinstance(result.data, dict):
            return result
        access_token = result.data.get("access_token")
        expires_in = result.data.get("expires_in")
        if not access_token or not isinstance(expires_in, (int, float)):
            return CatalogResult(
                "oauth/token", result.status, result.data, "invalid_token_response",
                "Token response omitted token or expiry",
            )
        self._token = str(access_token)
        self._token_expiry = now + float(expires_in)
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        self.token_path.write_text(
            json.dumps({"access_token": self._token, "expires_at": self._token_expiry}),
            encoding="utf-8",
        )
        return CatalogResult("oauth/token", 200, {"access_token": self._token})

    async def _igdb_ids(self, endpoint: str) -> CatalogResult:
        """Fetch a genre or theme ID list with seven-day disk caching."""
        key = f"igdb:{endpoint}"
        cached = self._cached_ids(key)
        if cached is not None:
            return CatalogResult(key, 200, cached)
        token = await self.igdb_token()
        if not token.ok:
            return token
        body = "fields id,name; limit 500;"
        result = await self._request(
            "POST", f"{IGDB_BASE}/{endpoint}", key,
            headers={"Client-ID": self.client_id, "Authorization": f"Bearer {self._token}"},
            content=body, igdb=True,
        )
        if result.ok and isinstance(result.data, list):
            self.cache.set(key, result.data)
        return result

    async def igdb_genres(self) -> CatalogResult:
        """Fetch IGDB genres."""
        return await self._igdb_ids("genres")

    async def igdb_themes(self) -> CatalogResult:
        """Fetch IGDB themes."""
        return await self._igdb_ids("themes")

    async def igdb_games(
        self, body: str, *, cold: bool = False
    ) -> CatalogResult:
        """Query games; cold calls bypass the in-memory result cache."""
        key = "igdb:games:" + body
        if not cold and key in self._result_cache:
            return self._result_cache[key]
        token = await self.igdb_token()
        if not token.ok:
            return token
        result = await self._request(
            "POST", f"{IGDB_BASE}/games", "games",
            headers={"Client-ID": self.client_id, "Authorization": f"Bearer {self._token}"},
            content=body, igdb=True,
        )
        if not cold:
            self._result_cache[key] = result
        return result

    async def measure_connect_tls(self, host: str) -> dict[str, float | str]:
        """Measure TCP and TLS durations without failing the catalog probe."""
        trace_times: dict[str, float] = {}

        async def trace(event_name: str, info: dict) -> None:
            if event_name.endswith("connect_tcp.started"):
                trace_times["tcp_started"] = time.perf_counter()
            elif event_name.endswith("connect_tcp.complete"):
                trace_times["tcp_complete"] = time.perf_counter()
            elif event_name.endswith("start_tls.started"):
                trace_times["tls_started"] = time.perf_counter()
            elif event_name.endswith("start_tls.complete"):
                trace_times["tls_complete"] = time.perf_counter()

        try:
            if host == "tmdb":
                if not self.tmdb_token:
                    raise ValueError("TMDB_READ_TOKEN is unset")
                await self.client.get(
                    f"{TMDB_BASE}/configuration",
                    headers={"Authorization": f"Bearer {self.tmdb_token}"},
                    extensions={"trace": trace},
                )
            elif host == "igdb":
                await self.limiter.wait()
                await self.client.get(
                    f"{IGDB_BASE}/",
                    extensions={"trace": trace},
                )
            else:
                raise ValueError(f"Unknown catalog host: {host}")
            tcp_ms = (
                trace_times["tcp_complete"] - trace_times["tcp_started"]
            ) * 1000
            tls_ms = (
                trace_times["tls_complete"] - trace_times["tls_started"]
            ) * 1000
            return {
                "tcp_ms": round(tcp_ms, 2),
                "tls_ms": round(tls_ms, 2),
                "connect_tls_ms": round(tcp_ms + tls_ms, 2),
            }
        except Exception as exc:
            return {"error": f"{type(exc).__name__}: {str(exc)[:120]}"}


def build_tmdb_params(genre_ids: list[int], keyword_ids: list[int]) -> dict[str, str | int]:
    """Build one TMDB discover query from resolved IDs."""
    params: dict[str, str | int] = {
        "include_adult": "false",
        "sort_by": "vote_average.desc",
        "vote_count.gte": 500,
        "page": 1,
    }
    if genre_ids:
        params["with_genres"] = ",".join(str(value) for value in genre_ids)
    if keyword_ids:
        params["with_keywords"] = ",".join(str(value) for value in keyword_ids)
    return params


def build_apicalypse(genre_ids: list[int], theme_ids: list[int]) -> str:
    """Build an IGDB games query using runtime-resolved genre/theme IDs."""
    clauses = ["total_rating_count >= 20"]
    if genre_ids:
        clauses.append(f"genres = ({','.join(str(value) for value in genre_ids)})")
    if theme_ids:
        clauses.append(f"themes = ({','.join(str(value) for value in theme_ids)})")
    return f"fields id,name,total_rating_count,rating; where {' & '.join(clauses)}; limit 50;"
