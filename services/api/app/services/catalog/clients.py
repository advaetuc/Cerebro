"""Async TMDB and IGDB clients for the catalog retrieval spike."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import random
import re
import tempfile
import time
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

TMDB_BASE = "https://api.themoviedb.org/3"
TWITCH_TOKEN_URL = "https://id.twitch.tv/oauth2/token"
IGDB_BASE = "https://api.igdb.com/v4"
API_DIR = Path(__file__).resolve().parents[3]
DEVELOPMENT_CACHE_DIR = API_DIR / "spike" / ".cache"
CACHE_DIR = DEVELOPMENT_CACHE_DIR / "catalog"
CACHE_TTL_SECONDS = 7 * 24 * 60 * 60
TMDB_MAX_RETRIES = 3
TMDB_BACKOFF_SECONDS = (0.2, 0.6, 1.2)


class ConfigError(ValueError):
    """Describe invalid local application configuration."""


def environment_mode() -> str:
    """Return the validated deployment environment."""
    value = clean_env_value("ENV") or "development"
    if value not in {"development", "production"}:
        raise ConfigError("ENV must be development or production")
    return value


def get_cache_dir() -> Path:
    """Resolve the configured disk-cache root for this process."""
    load_dotenv(dotenv_path=API_DIR / ".env", encoding="utf-8-sig")
    configured = clean_env_value("CACHE_DIR")
    if configured:
        return Path(configured).expanduser()
    if environment_mode() == "production":
        return Path("/tmp/cerebro-cache")
    return DEVELOPMENT_CACHE_DIR


def ensure_cache_directory(directory: Path) -> bool:
    """Probe cache writability and fall back to memory when unavailable."""
    try:
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=directory, prefix=".cerebro-check-", delete=True):
            pass
    except OSError as exc:
        logging.getLogger("cerebro.cache").warning(
            "CACHE_DIR is not writable; using memory-only cache (%s: %s)",
            type(exc).__name__,
            str(exc)[:120],
        )
        return False
    return True


def clean_env_value(name: str, value: str | None = None) -> str:
    """Read an environment value after removing common copy/paste wrappers."""
    raw = os.getenv(name, "") if value is None else value
    return raw.strip().strip("\ufeff").strip().strip("\"'").strip()


def redact_secrets(message: str) -> str:
    """Remove configured credentials from upstream error messages."""
    cleaned = str(message)
    for name in ("LASTFM_API_KEY", "TMDB_READ_TOKEN", "TWITCH_CLIENT_ID", "TWITCH_CLIENT_SECRET"):
        secret = clean_env_value(name)
        if len(secret) >= 4:
            cleaned = cleaned.replace(secret, "[redacted]")
    cleaned = re.sub(r"(?i)\bBearer\s+\S+", "Bearer [redacted]", cleaned)
    cleaned = re.sub(
        r"(?i)(api_key|access_token|client_secret|token)=\S+",
        r"\1=[redacted]",
        cleaned,
    )
    return cleaned.replace("\n", " ")


def build_headers(values: dict[str, tuple[str, str]]) -> dict[str, str]:
    """Build ASCII-safe headers from (environment name, value) pairs."""
    headers: dict[str, str] = {}
    for header, (env_name, value) in values.items():
        cleaned = clean_env_value(env_name, value)
        try:
            cleaned.encode("ascii")
        except UnicodeEncodeError as exc:
            raise ConfigError(
                f"{env_name} contains non-ASCII characters; re-copy the full token"
            ) from exc
        headers[header] = cleaned
    return headers


def token_is_valid(expires_at: float, now: float | None = None) -> bool:
    """Return whether a token remains valid beyond the 60-second safety margin."""
    return (time.time() if now is None else now) < expires_at - 60


@dataclass(frozen=True)
class CatalogResult:
    """Represent successful data or an HTTP/API failure without raising it."""

    endpoint: str
    status: int | str | None
    data: Any
    error_code: str | int | None = None
    error_message: str | None = None
    error_detail: str | None = None
    attempt: int = 1
    latency_ms: float = 0.0
    limiter_wait_ms: float = 0.0
    api_ms: float = 0.0
    retry_after: str | None = None

    @property
    def ok(self) -> bool:
        """Return whether the response has no transport or API error."""
        return isinstance(self.status, int) and self.status < 400 and self.error_code is None


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


class AsyncTokenBucket:
    """Limit IGDB throughput with a refillable token bucket."""

    def __init__(self, capacity: float = 4, refill_rate: float = 4) -> None:
        self.capacity = capacity
        self.refill_rate = refill_rate
        self.tokens = capacity
        self.updated_at = time.monotonic()
        self.lock = asyncio.Lock()

    async def acquire(self) -> float:
        """Consume one token and return time spent waiting in milliseconds."""
        wait_started = time.perf_counter()
        async with self.lock:
            while True:
                now = time.monotonic()
                elapsed = max(0.0, now - self.updated_at)
                self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)
                self.updated_at = now
                if self.tokens >= 1:
                    self.tokens -= 1
                    return (time.perf_counter() - wait_started) * 1000
                await asyncio.sleep((1 - self.tokens) / self.refill_rate)


def tmdb_backoff_delay(
    retry_index: int,
    retry_after: str | None = None,
    *,
    jitter: float | None = None,
) -> float:
    """Return a bounded exponential retry delay, honoring Retry-After."""
    if retry_after:
        try:
            return max(0.0, float(retry_after))
        except ValueError:
            try:
                retry_at = parsedate_to_datetime(retry_after).timestamp()
                return max(0.0, retry_at - time.time())
            except (TypeError, ValueError, OverflowError):
                pass
    base = TMDB_BACKOFF_SECONDS[min(retry_index, len(TMDB_BACKOFF_SECONDS) - 1)]
    spread = random.uniform(0.0, base * 0.1) if jitter is None else jitter
    return base + spread


def select_keyword_match(results: list[dict[str, Any]], keyword: str) -> dict[str, Any] | None:
    """Return only a case-insensitive exact keyword-name match."""
    wanted = keyword.strip().casefold()
    for result in results:
        if str(result.get("name", "")).strip().casefold() == wanted:
            return result
    return None


def _warm_copy(result: CatalogResult) -> CatalogResult:
    """Return cached result data with no network or limiter latency."""
    return CatalogResult(
        endpoint=result.endpoint,
        status=result.status,
        data=result.data,
        error_code=result.error_code,
        error_message=result.error_message,
        error_detail=result.error_detail,
        attempt=result.attempt,
    )


class JsonDiskCache:
    """Cache catalog ID lists with a bounded TTL."""

    def __init__(self, directory: Path | None = None, ttl: int = CACHE_TTL_SECONDS) -> None:
        self.directory = directory or get_cache_dir() / "catalog"
        self.ttl = ttl
        self._memory: dict[str, dict[str, Any]] = {}
        self._memory_only = not ensure_cache_directory(self.directory)

    def _path(self, key: str) -> Path:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return self.directory / f"{digest}.json"

    def get(self, key: str) -> Any | None:
        """Return an unexpired cached value, if present."""
        payload = self._read_entry(key)
        if payload is None:
            return None
        if time.time() - float(payload["saved_at"]) > self.ttl:
            return None
        return payload["value"]

    def get_stale(self, key: str) -> Any | None:
        """Return cached data even when its normal TTL has expired."""
        payload = self._read_entry(key)
        return payload.get("value") if payload else None

    def _read_entry(self, key: str) -> dict[str, Any] | None:
        if key in self._memory:
            return self._memory[key]
        if self._memory_only:
            return None
        try:
            value = json.loads(self._path(key).read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else None
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def set(self, key: str, value: Any) -> None:
        """Persist an ID-list value with its creation time."""
        payload = {"saved_at": time.time(), "value": value}
        self._memory[key] = payload
        if self._memory_only:
            return
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            self._path(key).write_text(json.dumps(payload), encoding="utf-8")
        except OSError as exc:
            self._memory_only = True
            logging.getLogger("cerebro.cache").warning(
                "CACHE_DIR became unwritable; using memory-only cache (%s: %s)",
                type(exc).__name__,
                str(exc)[:120],
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
    if isinstance(payload, list):
        for first in payload:
            if isinstance(first, dict) and (first.get("status") == "error" or first.get("error")):
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
        retry_after=response.headers.get("Retry-After"),
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
        igdb_bucket: AsyncTokenBucket | None = None,
        tmdb_concurrency: int = 2,
        igdb_concurrency: int = 8,
        token_path: Path | None = None,
    ) -> None:
        load_dotenv(dotenv_path=API_DIR / ".env", encoding="utf-8-sig")
        self.tmdb_token = clean_env_value("TMDB_READ_TOKEN", tmdb_token)
        self.client_id = clean_env_value("TWITCH_CLIENT_ID", twitch_client_id)
        self.client_secret = clean_env_value("TWITCH_CLIENT_SECRET", twitch_client_secret)
        self.client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10.0, read=20.0, write=20.0, pool=10.0),
            limits=httpx.Limits(
                max_connections=20,
                max_keepalive_connections=10,
                keepalive_expiry=30.0,
            ),
        )
        self._owns_client = client is None
        self.cache = cache or JsonDiskCache()
        self.limiter = limiter or AsyncRateLimiter(4.0)
        self.igdb_bucket = igdb_bucket or AsyncTokenBucket(4, 4)
        self.tmdb_semaphore = asyncio.Semaphore(tmdb_concurrency)
        self.igdb_semaphore = asyncio.Semaphore(igdb_concurrency)
        self.token_path = token_path or get_cache_dir() / "catalog" / "igdb_token.json"
        self._token: str | None = None
        self._token_expiry = 0.0
        self._token_lock = asyncio.Lock()
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
        is_tmdb = url.startswith(TMDB_BASE)
        semaphore = self.igdb_semaphore if igdb else self.tmdb_semaphore if is_tmdb else None
        limiter_wait_ms = 0.0
        api_ms = 0.0
        last_result: CatalogResult | None = None
        last_detail: str | None = None
        attempts = TMDB_MAX_RETRIES + 1 if is_tmdb else 1
        for attempt in range(1, attempts + 1):
            try:
                if semaphore is None:
                    result, wait_ms, call_ms = await self._request_once(
                        method, url, endpoint, headers, params, content, data, igdb, extensions
                    )
                else:
                    semaphore_started = time.perf_counter()
                    async with semaphore:
                        semaphore_wait_ms = (time.perf_counter() - semaphore_started) * 1000
                        result, wait_ms, call_ms = await self._request_once(
                            method, url, endpoint, headers, params, content, data, igdb, extensions
                        )
                    wait_ms += semaphore_wait_ms
                limiter_wait_ms += wait_ms
                api_ms += call_ms
                last_result = result
                if result.status == "no_match":
                    return result
                if result.status is None and result.error_code == "transport_error":
                    if not is_tmdb or attempt == attempts:
                        return self._with_metrics(
                            result,
                            attempt,
                            started,
                            limiter_wait_ms,
                            api_ms,
                            result.error_detail,
                        )
                    await asyncio.sleep(tmdb_backoff_delay(attempt - 1))
                    continue
                retry_after = result.retry_after
                if result.error_code is not None:
                    retryable = result.error_code == 429 or (
                        isinstance(result.error_code, int) and result.error_code >= 500
                    )
                else:
                    retryable = isinstance(result.status, int) and (
                        result.status == 429 or result.status >= 500
                    )
                if not retryable or attempt == attempts:
                    detail = result.error_detail
                    if result.status is not None and result.status != "no_match" and not result.ok:
                        status_error = (
                            f"HTTPStatusError: HTTP {result.status}: {result.error_message or ''}"
                        )
                        detail = detail or status_error[:120]
                    return self._with_metrics(
                        result, attempt, started, limiter_wait_ms, api_ms, detail
                    )
                await asyncio.sleep(tmdb_backoff_delay(attempt - 1, retry_after))
            except httpx.HTTPError as exc:
                last_detail = f"{type(exc).__name__}: {str(exc)[:120]}"
                return CatalogResult(
                    endpoint=endpoint,
                    status=None,
                    data=None,
                    error_code="transport_error",
                    error_message=str(exc)[:120],
                    error_detail=last_detail,
                    attempt=attempt,
                    latency_ms=(time.perf_counter() - started) * 1000,
                    limiter_wait_ms=round(limiter_wait_ms, 2),
                    api_ms=round(api_ms, 2),
                )
        if last_result is not None:
            return self._with_metrics(
                last_result, attempts, started, limiter_wait_ms, api_ms, last_detail
            )
        return CatalogResult(endpoint, None, None, "transport_error", last_detail, last_detail)

    async def _request_once(
        self,
        method: str,
        url: str,
        endpoint: str,
        headers: dict[str, str] | None,
        params: dict[str, str | int] | None,
        content: str | None,
        data: dict[str, str] | None,
        igdb: bool,
        extensions: dict[str, Any] | None,
    ) -> tuple[CatalogResult, float, float]:
        """Perform one request and track limiter wait separately from API time."""
        wait_started = time.perf_counter()
        if igdb:
            await self.igdb_bucket.acquire()
        wait_ms = (time.perf_counter() - wait_started) * 1000 if igdb else 0.0
        api_started = time.perf_counter()
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
            call_ms = (time.perf_counter() - api_started) * 1000
            detail = f"{type(exc).__name__}: {str(exc)[:120]}"
            return (
                CatalogResult(
                    endpoint,
                    None,
                    None,
                    "transport_error",
                    str(exc)[:120],
                    error_detail=detail,
                ),
                wait_ms,
                call_ms,
            )
        call_ms = (time.perf_counter() - api_started) * 1000
        return _decode(response, endpoint, api_started), wait_ms, call_ms

    @staticmethod
    def _with_metrics(
        result: CatalogResult,
        attempt: int,
        started: float,
        limiter_wait_ms: float,
        api_ms: float,
        error_detail: str | None,
    ) -> CatalogResult:
        """Attach attempts and timing data to a final request result."""
        return CatalogResult(
            endpoint=result.endpoint,
            status=result.status,
            data=result.data,
            error_code=result.error_code,
            error_message=result.error_message,
            error_detail=error_detail,
            attempt=attempt,
            latency_ms=(time.perf_counter() - started) * 1000,
            limiter_wait_ms=round(limiter_wait_ms, 2),
            api_ms=round(api_ms, 2),
        )

    async def tmdb_genres(self) -> CatalogResult:
        """Fetch the movie genre ID list, using the seven-day ID cache."""
        key = "tmdb:genre/movie/list"
        cached = self._cached_ids(key)
        if cached is not None:
            return CatalogResult(key, 200, {"genres": cached})
        if not self.tmdb_token:
            raise ConfigError("Set TMDB_READ_TOKEN in services/api/.env")
        result = await self._request(
            "GET",
            f"{TMDB_BASE}/genre/movie/list",
            key,
            headers=build_headers(
                {"Authorization": ("TMDB_READ_TOKEN", f"Bearer {self.tmdb_token}")}
            ),
            params={"language": "en-US"},
        )
        if result.ok and isinstance(result.data, dict):
            self.cache.set(key, result.data.get("genres", []))
        return result

    async def tmdb_configuration(self) -> CatalogResult:
        """Fetch TMDB image configuration with the existing ID cache."""
        key = "tmdb:configuration"
        cached = self._cached_ids(key)
        if cached is not None:
            return CatalogResult(key, 200, cached)
        if not self.tmdb_token:
            raise ConfigError("Set TMDB_READ_TOKEN in services/api/.env")
        result = await self._request(
            "GET",
            f"{TMDB_BASE}/configuration",
            key,
            headers=build_headers(
                {"Authorization": ("TMDB_READ_TOKEN", f"Bearer {self.tmdb_token}")}
            ),
        )
        if result.ok and isinstance(result.data, dict):
            self.cache.set(key, result.data)
        return result

    async def tmdb_keyword(self, name: str) -> CatalogResult:
        """Resolve a keyword name to IDs, using the seven-day ID cache."""
        key = f"tmdb:keyword:{name.casefold()}"
        cached = self._cached_ids(key)
        if cached is not None:
            if isinstance(cached, dict):
                cached = [cached]
            match = select_keyword_match(cached if isinstance(cached, list) else [], name)
            if match is None:
                self.cache.set(key, [])
                return CatalogResult(
                    key,
                    "no_match",
                    {"results": []},
                    error_message="No keyword match",
                )
            selected = {field: match[field] for field in ("id", "name") if field in match}
            self.cache.set(key, [selected])
            return CatalogResult(key, 200, {"results": [selected]})
        if not self.tmdb_token:
            raise ConfigError("Set TMDB_READ_TOKEN in services/api/.env")
        result = await self._request(
            "GET",
            f"{TMDB_BASE}/search/keyword",
            key,
            headers=build_headers(
                {"Authorization": ("TMDB_READ_TOKEN", f"Bearer {self.tmdb_token}")}
            ),
            params={"query": name, "page": 1},
        )
        if not result.ok or not isinstance(result.data, dict):
            return result
        raw_results = result.data.get("results", [])
        matches = raw_results if isinstance(raw_results, list) else []
        match = select_keyword_match(matches, name)
        if match is None:
            self.cache.set(key, [])
            return CatalogResult(
                key,
                "no_match",
                {"results": []},
                error_message="No keyword match",
                attempt=result.attempt,
                latency_ms=result.latency_ms,
                limiter_wait_ms=result.limiter_wait_ms,
                api_ms=result.api_ms,
                error_detail=result.error_detail,
                retry_after=result.retry_after,
            )
        selected = {field: match[field] for field in ("id", "name") if field in match}
        self.cache.set(key, [selected])
        return CatalogResult(
            key,
            result.status,
            {"results": [selected]},
            attempt=result.attempt,
            latency_ms=result.latency_ms,
            limiter_wait_ms=result.limiter_wait_ms,
            api_ms=result.api_ms,
            error_detail=result.error_detail,
            retry_after=result.retry_after,
        )

    async def tmdb_discover(
        self, params: dict[str, str | int], *, cold: bool = False
    ) -> CatalogResult:
        """Discover movies; cold calls bypass the in-memory result cache."""
        key = "tmdb:discover:" + json.dumps(params, sort_keys=True, separators=(",", ":"))
        if not cold and key in self._result_cache:
            return _warm_copy(self._result_cache[key])
        if not self.tmdb_token:
            raise ConfigError("Set TMDB_READ_TOKEN in services/api/.env")
        result = await self._request(
            "GET",
            f"{TMDB_BASE}/discover/movie",
            "discover/movie",
            headers=build_headers(
                {"Authorization": ("TMDB_READ_TOKEN", f"Bearer {self.tmdb_token}")}
            ),
            params=params,
        )
        if not cold:
            self._result_cache[key] = result
        return result

    async def tmdb_search_movie(self, title: str, year: str | None = None) -> CatalogResult:
        """Search for a film anchor by title and optional release year."""
        if not self.tmdb_token:
            raise ConfigError("Set TMDB_READ_TOKEN in services/api/.env")
        params: dict[str, str | int] = {"query": title, "page": 1}
        if year:
            params["year"] = year
        key = "tmdb:search/movie:" + json.dumps(params, sort_keys=True)
        cached = self._cached_ids(key)
        if cached is not None:
            return CatalogResult("search/movie", 200, {"results": cached})
        result = await self._request(
            "GET",
            f"{TMDB_BASE}/search/movie",
            "search/movie",
            headers=build_headers(
                {"Authorization": ("TMDB_READ_TOKEN", f"Bearer {self.tmdb_token}")}
            ),
            params=params,
        )
        if result.ok and isinstance(result.data, dict):
            self.cache.set(key, result.data.get("results", []))
        return result

    async def tmdb_movie_recommendations(self, movie_id: int) -> CatalogResult:
        """Fetch first-page recommendations for a known film anchor."""
        if not self.tmdb_token:
            raise ConfigError("Set TMDB_READ_TOKEN in services/api/.env")
        return await self._request(
            "GET",
            f"{TMDB_BASE}/movie/{movie_id}/recommendations",
            "movie/recommendations",
            headers=build_headers(
                {"Authorization": ("TMDB_READ_TOKEN", f"Bearer {self.tmdb_token}")}
            ),
            params={"page": 1},
        )

    async def igdb_token(self) -> CatalogResult:
        """Load or obtain the Twitch client-credentials token."""
        async with self._token_lock:
            return await self._get_igdb_token()

    async def _get_igdb_token(self) -> CatalogResult:
        """Read or refresh the Twitch token while holding its lock."""
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
            raise ConfigError("Set TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET in services/api/.env")
        result = await self._request(
            "POST",
            TWITCH_TOKEN_URL,
            "oauth/token",
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
                "oauth/token",
                result.status,
                result.data,
                "invalid_token_response",
                "Token response omitted token or expiry",
                error_detail="ValueError: Token response omitted token or expiry",
                attempt=result.attempt,
            )
        self._token = str(access_token)
        self._token_expiry = now + float(expires_in)
        try:
            self.token_path.parent.mkdir(parents=True, exist_ok=True)
            self.token_path.write_text(
                json.dumps({"access_token": self._token, "expires_at": self._token_expiry}),
                encoding="utf-8",
            )
        except OSError as exc:
            logging.getLogger("cerebro.cache").warning(
                "CACHE_DIR is not writable; keeping IGDB token in memory (%s: %s)",
                type(exc).__name__,
                str(exc)[:120],
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
            "POST",
            f"{IGDB_BASE}/{endpoint}",
            key,
            headers=build_headers(
                {
                    "Client-ID": ("TWITCH_CLIENT_ID", self.client_id),
                    "Authorization": ("TWITCH_CLIENT_ID", f"Bearer {self._token}"),
                }
            ),
            content=body,
            igdb=True,
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

    async def igdb_games(self, body: str, *, cold: bool = False) -> CatalogResult:
        """Query games; cold calls bypass the in-memory result cache."""
        key = "igdb:games:" + body
        if not cold and key in self._result_cache:
            return _warm_copy(self._result_cache[key])
        token = await self.igdb_token()
        if not token.ok:
            return token
        result = await self._request(
            "POST",
            f"{IGDB_BASE}/games",
            "games",
            headers=build_headers(
                {
                    "Client-ID": ("TWITCH_CLIENT_ID", self.client_id),
                    "Authorization": ("TWITCH_CLIENT_ID", f"Bearer {self._token}"),
                }
            ),
            content=body,
            igdb=True,
        )
        if not cold:
            self._result_cache[key] = result
        return result

    async def igdb_search_game(self, name: str) -> CatalogResult:
        """Search IGDB for an anchor game and its similar-game IDs."""
        key = f"igdb:search-game:{name.casefold()}"
        cached = self._cached_ids(key)
        if cached is not None:
            return CatalogResult("search-game", 200, cached)
        escaped = name.replace('"', '\\"')
        body = f'search "{escaped}"; fields id,name,similar_games; limit 5;'
        result = await self.igdb_games(body)
        if result.ok and isinstance(result.data, list):
            self.cache.set(key, result.data)
        return result

    async def igdb_games_by_ids(
        self,
        ids: list[int],
        *,
        rating_count_floor: int | None = 100,
        rating_floor: int | None = 65,
    ) -> CatalogResult:
        """Fetch full candidate records for similar-game IDs."""
        if not ids:
            return CatalogResult("games", 200, [])
        clauses = [f"id = ({','.join(map(str, ids))})"]
        if rating_count_floor is not None:
            clauses.append(f"total_rating_count >= {rating_count_floor}")
        if rating_floor is not None:
            clauses.append(f"total_rating >= {rating_floor}")
        body = (
            "fields id,name,first_release_date,total_rating,total_rating_count,"
            "genres,themes,cover.image_id; "
            f"where {' & '.join(clauses)}; limit 40; sort total_rating desc;"
        )
        return await self.igdb_games(body)

    async def igdb_multiquery(self, body: str, *, cold: bool = False) -> CatalogResult:
        """Submit several IGDB games subqueries in one HTTP request."""
        key = "igdb:multiquery:" + body
        if not cold and key in self._result_cache:
            return _warm_copy(self._result_cache[key])
        token = await self.igdb_token()
        if not token.ok:
            return token
        result = await self._request(
            "POST",
            f"{IGDB_BASE}/multiquery",
            "multiquery",
            headers=build_headers(
                {
                    "Client-ID": ("TWITCH_CLIENT_ID", self.client_id),
                    "Authorization": ("TWITCH_CLIENT_ID", f"Bearer {self._token}"),
                }
            ),
            content=body,
            igdb=True,
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
                    raise ConfigError("Set TMDB_READ_TOKEN in services/api/.env")
                result = await self._request(
                    "GET",
                    f"{TMDB_BASE}/configuration",
                    "configuration",
                    headers=build_headers(
                        {"Authorization": ("TMDB_READ_TOKEN", f"Bearer {self.tmdb_token}")}
                    ),
                    extensions={"trace": trace},
                )
            elif host == "igdb":
                result = await self._request(
                    "GET",
                    f"{IGDB_BASE}/",
                    "igdb:connect-check",
                    igdb=True,
                    extensions={"trace": trace},
                )
            else:
                raise ValueError(f"Unknown catalog host: {host}")
            if result.status is None and result.error_detail:
                return {"error": result.error_detail[:120]}
            tcp_ms = (trace_times["tcp_complete"] - trace_times["tcp_started"]) * 1000
            tls_ms = (trace_times["tls_complete"] - trace_times["tls_started"]) * 1000
            return {
                "tcp_ms": round(tcp_ms, 2),
                "tls_ms": round(tls_ms, 2),
                "connect_tls_ms": round(tcp_ms + tls_ms, 2),
            }
        except Exception as exc:
            return {"error": f"{type(exc).__name__}: {str(exc)[:120]}"}


def build_tmdb_params(
    genre_ids: list[int],
    keyword_ids: list[int],
    page: int = 1,
    date_range: tuple[str, str] | None = None,
) -> dict[str, str | int]:
    """Build one TMDB discover query from resolved IDs."""
    params: dict[str, str | int] = {
        "include_adult": "false",
        "sort_by": "popularity.desc",
        "vote_count.gte": 500,
        "vote_average.gte": 6,
        "page": page,
    }
    if genre_ids:
        params["with_genres"] = "|".join(str(value) for value in genre_ids)
    if keyword_ids:
        params["with_keywords"] = "|".join(str(value) for value in keyword_ids)
    if date_range:
        params["primary_release_date.gte"] = date_range[0]
        params["primary_release_date.lte"] = date_range[1]
    return params


def build_apicalypse(genre_ids: list[int], theme_ids: list[int], mode: str = "combined") -> str:
    """Build an IGDB games query using runtime-resolved genre/theme IDs."""
    clauses = ["total_rating_count >= 100", "total_rating >= 65"]
    if genre_ids and mode in {"combined", "genres"}:
        clauses.append(f"genres = ({','.join(str(value) for value in genre_ids)})")
    if theme_ids and mode in {"combined", "themes"}:
        clauses.append(f"themes = ({','.join(str(value) for value in theme_ids)})")
    return (
        "fields id,name,first_release_date,total_rating,total_rating_count,genres,themes,"
        "cover.image_id; "
        f"where {' & '.join(clauses)}; sort total_rating desc; limit 40;"
    )


def build_multiquery(queries: list[tuple[str, str]]) -> str:
    """Combine labeled Apicalypse queries into one IGDB multiquery body."""
    return " ".join(f'query games "{label}" {{ {body} }};' for label, body in queries)
