"""FastAPI entry point for the no-UI Cerebro prototype."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import sys
import time
from collections import deque
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator, model_validator

from app.services.catalog.clients import (
    CatalogClients,
    ConfigError,
    clean_env_value,
    environment_mode,
    get_cache_dir,
    redact_secrets,
)
from app.services.ranking import RankingService
from app.services.vibe import ArtistInput, VibeResolver
from spike.lastfm_client import LastFmClient, LastFmResult

ANALYZE_CACHE_SECONDS = 15 * 60
API_ROOT = Path(__file__).resolve().parents[1]
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]{2,32}$")
DEVELOPMENT_CORS_REGEX = r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$"
logger = logging.getLogger("cerebro.upstream")
request_logger = logging.getLogger("cerebro.request")
ATTRIBUTION = [
    "Data provided by Last.fm",
    "This product uses the TMDB API but is not endorsed or certified by TMDB.",
    "IGDB data provided by Twitch Interactive, Inc.",
]
JUNK_TAGS = frozenset(
    {"seen live", "favorites", "favourite", "favourites", "albums i own", "spotify"}
)

load_dotenv(dotenv_path=API_ROOT / ".env", encoding="utf-8-sig")


def cors_settings(
    mode: str | None = None,
    origins: str | None = None,
    origin_regex: str | None = None,
) -> dict[str, Any]:
    """Build exact CORS origins and the optional origin regular expression."""
    environment = mode or clean_env_value("ENV") or "development"
    origin_value = os.getenv("CORS_ORIGINS", "") if origins is None else origins
    regex_value = (
        os.getenv("CORS_ORIGIN_REGEX", "")
        if origin_regex is None
        else origin_regex
    ).strip()
    allow_regex = regex_value or None
    if environment == "development":
        allow_regex = (
            f"(?:{DEVELOPMENT_CORS_REGEX})|(?:{regex_value})"
            if regex_value
            else DEVELOPMENT_CORS_REGEX
        )
    return {
        "allow_origins": [origin.strip() for origin in origin_value.split(",") if origin.strip()],
        "allow_origin_regex": allow_regex,
        "allow_credentials": True,
        "allow_methods": ["GET", "POST"],
        "allow_headers": ["Content-Type", "Authorization"],
    }


class AnalysisGuard:
    """Enforce per-IP sliding windows and a process-wide analysis semaphore."""

    def __init__(self, *, per_minute: int = 10, per_hour: int = 60) -> None:
        self.per_minute = per_minute
        self.per_hour = per_hour
        self.requests: dict[str, deque[float]] = {}
        self.semaphore = asyncio.Semaphore(3)

    def check_ip(self, client_ip: str, now: float | None = None) -> None:
        timestamp = time.monotonic() if now is None else now
        history = self.requests.setdefault(client_ip, deque())
        while history and timestamp - history[0] >= 3600:
            history.popleft()
        minute = [event for event in history if timestamp - event < 60]
        wait_seconds = 0.0
        if len(minute) >= self.per_minute:
            wait_seconds = max(wait_seconds, 60 - (timestamp - minute[0]))
        if len(history) >= self.per_hour:
            wait_seconds = max(wait_seconds, 3600 - (timestamp - history[0]))
        if wait_seconds > 0:
            raise RateLimitError(math.ceil(wait_seconds))
        history.append(timestamp)


class RateLimitError(RuntimeError):
    """Describe a temporary analysis rate or concurrency limit."""

    def __init__(self, retry_after: int) -> None:
        self.retry_after = max(1, retry_after)
        super().__init__("Too many analyses are running. Please wait a moment and try again.")


class UserNotFoundError(RuntimeError):
    """Represent Last.fm's unknown-user response."""


class EmptyListeningError(RuntimeError):
    """Represent a private profile or profile without public listening history."""


app = FastAPI(title="Cerebro API", version="0.1.0")
app.add_middleware(CORSMiddleware, **cors_settings())
app.state.analysis_guard = AnalysisGuard()
app.state.analyze_cache = {}


@app.middleware("http")
async def access_log(request: Request, call_next):
    """Emit one production request line without query-string data."""
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        if (clean_env_value("ENV") or "development") == "production":
            request_logger.info(
                "request method=%s path=%s status=500 duration_ms=%.2f",
                request.method,
                request.url.path,
                (time.perf_counter() - started) * 1000,
            )
        raise
    if (clean_env_value("ENV") or "development") == "production":
        request_logger.info(
            "request method=%s path=%s status=%s duration_ms=%.2f",
            request.method,
            request.url.path,
            response.status_code,
            (time.perf_counter() - started) * 1000,
        )
    return response


class AnalyzeRequest(BaseModel):
    """Accept either a Last.fm username or exactly three seed artists."""

    mode: str
    username: str | None = None
    artists: list[str] | None = None

    @field_validator("username", mode="before")
    @classmethod
    def trim_username(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value

    @field_validator("artists", mode="before")
    @classmethod
    def trim_artist_names(cls, value: Any) -> Any:
        return [artist.strip() if isinstance(artist, str) else artist for artist in value] \
            if isinstance(value, list) else value

    @model_validator(mode="after")
    def validate_mode_input(self) -> AnalyzeRequest:
        if self.mode == "lastfm":
            if self.username is None or not USERNAME_PATTERN.fullmatch(self.username):
                raise ValueError("Last.fm username must be 2–32 letters, numbers, '_' or '-'.")
        if self.mode == "seed":
            artists = self.artists or []
            if len(artists) != 3:
                raise ValueError("Choose exactly three distinct artists.")
            if any(not name or len(name) > 100 for name in artists):
                raise ValueError("Each artist name must be between 1 and 100 characters.")
            if len({name.casefold() for name in artists}) != 3:
                raise ValueError("Choose exactly three distinct artists.")
        return self


class UpstreamError(RuntimeError):
    """Represent an unsuccessful upstream service response."""

    def __init__(
        self, provider: str, endpoint: str, reason: str, exception_class: str = "UpstreamError"
    ) -> None:
        self.provider = provider
        self.endpoint = endpoint
        self.reason = _safe_reason(reason)
        self.exception_class = exception_class
        super().__init__(self.reason)


def _safe_reason(value: str) -> str:
    return redact_secrets(value)[:160]


def _log_upstream_failure(provider: str, endpoint: str, exception: str, message: str) -> None:
    logger.warning(
        "upstream failure provider=%s endpoint=%s exception=%s message=%s",
        provider,
        endpoint,
        exception[:60],
        _safe_reason(message)[:120],
    )


@app.on_event("startup")
async def startup() -> None:
    """Initialize shared clients and preserve clear startup configuration errors."""
    load_dotenv(dotenv_path=API_ROOT / ".env", encoding="utf-8-sig")
    app.state.config_error = None
    app.state.analyze_cache = {}
    app.state.analysis_guard = AnalysisGuard()
    app.state.lastfm = None
    app.state.catalog = None
    app.state.ranker = None
    try:
        if environment_mode() == "production":
            request_logger.setLevel(logging.INFO)
        get_cache_dir()
        app.state.catalog = CatalogClients()
        app.state.ranker = RankingService(app.state.catalog)
    except ConfigError as exc:
        app.state.config_error = str(exc)
        print(str(exc), file=sys.stderr)
    if app.state.config_error:
        return
    if clean_env_value("LASTFM_API_KEY"):
        try:
            app.state.lastfm = LastFmClient()
        except ConfigError as exc:
            app.state.config_error = str(exc)
            print(str(exc), file=sys.stderr)


@app.on_event("shutdown")
async def shutdown() -> None:
    """Close shared upstream clients."""
    catalog = getattr(app.state, "catalog", None)
    if catalog is not None:
        await catalog.close()
    lastfm = getattr(app.state, "lastfm", None)
    if lastfm is not None:
        lastfm.close()


@app.exception_handler(ConfigError)
async def config_error_handler(request: Request, exc: ConfigError) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": str(exc)})


@app.exception_handler(UserNotFoundError)
async def user_not_found_handler(request: Request, exc: UserNotFoundError) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={
            "message": "We couldn't find that Last.fm username. Check the spelling "
            "and that the profile is public."
        },
    )


@app.exception_handler(EmptyListeningError)
async def empty_listening_handler(request: Request, exc: EmptyListeningError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={"message": str(exc) or "This profile has no public listening history yet."},
    )


@app.exception_handler(RateLimitError)
async def rate_limit_handler(request: Request, exc: RateLimitError) -> JSONResponse:
    return JSONResponse(
        status_code=429,
        headers={"Retry-After": str(exc.retry_after)},
        content={"message": str(exc)},
    )


@app.exception_handler(UpstreamError)
async def upstream_error_handler(request: Request, exc: UpstreamError) -> JSONResponse:
    _log_upstream_failure(exc.provider, exc.endpoint, exc.exception_class, exc.reason)
    return JSONResponse(
        status_code=502,
        content={"provider": exc.provider, "reason": exc.reason},
    )


@app.exception_handler(httpx.HTTPError)
async def http_error_handler(request: Request, exc: httpx.HTTPError) -> JSONResponse:
    _log_upstream_failure("upstream", "unknown", type(exc).__name__, str(exc))
    return JSONResponse(status_code=502, content={
        "provider": "upstream", "reason": "An upstream service request failed."
    })


async def analysis_guard(request: Request) -> AsyncIterator[None]:
    """Apply per-IP limits and reserve one of three global analysis slots."""
    limiter = getattr(request.app.state, "analysis_guard", None)
    if limiter is None:
        limiter = request.app.state.analysis_guard = AnalysisGuard()
    forwarded = request.headers.get("x-forwarded-for", "")
    client_ip = forwarded.split(",", 1)[0].strip()
    if not client_ip:
        client_ip = request.client.host if request.client else "unknown"
    limiter.check_ip(client_ip)
    try:
        await asyncio.wait_for(limiter.semaphore.acquire(), timeout=15)
    except TimeoutError as exc:
        raise RateLimitError(15) from exc
    try:
        yield
    finally:
        limiter.semaphore.release()


def get_lastfm_client(request: Request) -> LastFmClient:
    """Return the shared Last.fm client or raise its saved config error."""
    message = getattr(request.app.state, "config_error", None)
    if message:
        raise ConfigError(message)
    client = getattr(request.app.state, "lastfm", None)
    return client if client is not None else LastFmClient()


def get_catalog_clients(request: Request) -> CatalogClients:
    """Return the shared catalog client or create one for dependency overrides."""
    message = getattr(request.app.state, "config_error", None)
    if message:
        raise ConfigError(message)
    client = getattr(request.app.state, "catalog", None)
    return client if client is not None else CatalogClients()


def get_ranking_service(
    request: Request,
    catalog: CatalogClients = Depends(get_catalog_clients),  # noqa: B008
) -> RankingService:
    """Return the shared ranker tied to the shared catalog client."""
    ranker = getattr(request.app.state, "ranker", None) if request else None
    return ranker if ranker is not None else RankingService(catalog)


def _items(result: LastFmResult, *path: str) -> list[dict[str, Any]]:
    value: Any = result.data
    for key in path:
        value = value.get(key, {}) if isinstance(value, dict) else {}
    if isinstance(value, dict):
        value = [value] if value else []
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _lastfm_failure(result: LastFmResult) -> tuple[str, str]:
    """Return a safe exception class and concise upstream failure reason."""
    message = result.error_message or f"HTTP {result.status}"
    if result.status is None:
        exception, separator, reason = message.partition(": ")
        if separator and exception.isidentifier():
            return exception, reason[:120]
        return "TransportError", message[:120]
    return (
        "HTTPStatusError" if result.status >= 400 else "LastFmAPIError",
        message[:120],
    )


def _require_success(result: LastFmResult, *, user_lookup: bool = False) -> None:
    if not result.ok:
        message = result.error_message or ""
        if user_lookup and result.error_code == 6:
            raise UserNotFoundError(message)
        if user_lookup and (
            result.error_code == 17
            or result.status == 403
            or "private" in message.casefold()
        ):
            raise EmptyListeningError(
                "This Last.fm profile is private or has no public listening history."
            )
        exception, reason = _lastfm_failure(result)
        raise UpstreamError("Last.fm", result.endpoint, reason, exception)


def _clean_artist_tags(raw: list[dict[str, Any]], artist_name: str) -> list[dict[str, Any]]:
    cleaned = []
    seen = set()
    for item in raw:
        name = str(item.get("name", "")).strip()
        key = name.casefold()
        try:
            weight = float(item.get("count", item.get("weight", 0)))
        except (TypeError, ValueError):
            weight = 0.0
        if not name or weight < 20 or key == artist_name.strip().casefold():
            continue
        if key in JUNK_TAGS or key in seen:
            continue
        cleaned.append({"name": name, "weight": weight})
        seen.add(key)
    return cleaned


def _artist_payload(result: LastFmResult) -> dict[str, Any]:
    _require_success(result)
    artist = result.data.get("artist", {})
    return artist if isinstance(artist, dict) else {}


def _log_lastfm_result_failure(result: LastFmResult) -> None:
    exception, message = _lastfm_failure(result)
    _log_upstream_failure(
        "Last.fm", result.endpoint, exception, message,
    )


def _tag_payload(result: LastFmResult) -> list[dict[str, Any]]:
    if not result.ok:
        _require_success(result)
    return _items(result, "toptags", "tag")


def _top_artists(client: LastFmClient, username: str) -> list[dict[str, Any]]:
    combined: dict[str, dict[str, Any]] = {}
    for period in ("6month", "overall"):
        result = client.user_top_artists(username, period, limit=50)
        _require_success(result, user_lookup=True)
        for item in _items(result, "topartists", "artist"):
            name = str(item.get("name", "")).strip()
            if not name:
                continue
            key = name.casefold()
            try:
                playcount = int(item.get("playcount", 0) or 0)
            except (TypeError, ValueError):
                playcount = 0
            existing = combined.setdefault(key, {"name": name, "play_weight": 0})
            existing["play_weight"] += playcount
    return sorted(combined.values(), key=lambda item: item["play_weight"], reverse=True)[:50]


def _resolve_artist(
    client: LastFmClient, artist: dict[str, Any], *, equal_weight: bool = False
) -> ArtistInput:
    name = str(artist["name"])
    direct_result = client.artist_top_tags(name)
    direct = _clean_artist_tags(_tag_payload(direct_result), name)
    borrowed = False
    tags = direct
    if len(direct) < 3:
        similar_result = client.artist_similar(name, limit=5)
        if not similar_result.ok:
            _log_lastfm_result_failure(similar_result)
        similar = _items(similar_result, "similarartists", "artist") if similar_result.ok else []
        known = {tag["name"].casefold() for tag in tags}
        for similar_artist in similar[:5]:
            similar_name = str(similar_artist.get("name", "")).strip()
            if not similar_name:
                continue
            borrowed_result = client.artist_top_tags(similar_name)
            if not borrowed_result.ok:
                continue
            borrowed_tags = _clean_artist_tags(_tag_payload(borrowed_result), similar_name)
            for tag in borrowed_tags:
                key = tag["name"].casefold()
                if key not in known:
                    tags.append({**tag, "borrowed": True})
                    known.add(key)
        borrowed = bool(tags and len(direct) < 3)
    info_result = client.artist_get_info(name)
    if not info_result.ok:
        _log_lastfm_result_failure(info_result)
    info = _artist_payload(info_result) if info_result.ok else {}
    stats = info.get("stats", {})
    try:
        listeners = int(stats.get("listeners")) if isinstance(stats, dict) else None
    except (TypeError, ValueError):
        listeners = None
    try:
        play_weight = float(artist.get("play_weight", 1) or 1)
    except (TypeError, ValueError):
        play_weight = 1.0
    return ArtistInput(
        name=name,
        tags=tags,
        listeners=listeners,
        borrowed=borrowed,
        play_weight=1.0 if equal_weight else play_weight,
    )


@app.get("/health")
async def health(request: Request) -> dict[str, bool]:
    """Return process health and whether provider configuration is incomplete."""
    providers = (
        "LASTFM_API_KEY", "TMDB_READ_TOKEN", "TWITCH_CLIENT_ID", "TWITCH_CLIENT_SECRET"
    )
    degraded = bool(getattr(request.app.state, "config_error", None)) or any(
        not clean_env_value(name) for name in providers
    )
    return {"ok": True, "degraded": degraded}


@app.get("/artists/search")
async def search_artists(
    q: str,
    client: LastFmClient = Depends(get_lastfm_client),  # noqa: B008
) -> dict[str, Any]:
    """Search Last.fm artist names for Seed Mode."""
    try:
        result = client.artist_search(q, limit=8)
        _require_success(result)
        matches = _items(result, "results", "artistmatches", "artist")
        return {"artists": [
            {
                "name": item.get("name", ""),
                "listeners": item.get("listeners"),
                "url": item.get("url"),
            }
            for item in matches[:8]
        ]}
    except ConfigError:
        raise
    except UpstreamError:
        raise
    except Exception as exc:
        raise UpstreamError(
            "Last.fm", "artist.search", str(exc), type(exc).__name__
        ) from exc


def _analyze_key(payload: AnalyzeRequest) -> str:
    return json.dumps(payload.model_dump(), sort_keys=True, separators=(",", ":"))


@app.post("/analyze")
async def analyze(
    payload: AnalyzeRequest,
    request: Request,
    lastfm: LastFmClient = Depends(get_lastfm_client),  # noqa: B008
    ranker: RankingService = Depends(get_ranking_service),  # noqa: B008
    _guard: None = Depends(analysis_guard),  # noqa: B008
) -> dict[str, Any]:
    """Resolve listening input and return a ranked recommendation set."""
    if payload.mode not in {"lastfm", "seed"}:
        raise HTTPException(status_code=422, detail="Choose lastfm or seed mode.")
    cache = getattr(request.app.state, "analyze_cache", None)
    if cache is None:
        cache = request.app.state.analyze_cache = {}
    key = _analyze_key(payload)
    cached = cache.get(key)
    if cached and cached[0] > time.time():
        return cached[1]
    try:
        if payload.mode == "lastfm":
            username = (payload.username or "").strip()
            info_result = lastfm.user_get_info(username)
            _require_success(info_result, user_lookup=True)
            profile = info_result.data.get("user", {})
            profile = profile if isinstance(profile, dict) else {}
            try:
                playcount = int(profile.get("playcount", 0) or 0)
            except (TypeError, ValueError):
                playcount = 0
            if playcount <= 0:
                raise EmptyListeningError(
                    "This Last.fm profile has no public listening history yet."
                )
            artists = _top_artists(lastfm, username)
            if len(artists) < 5:
                raise EmptyListeningError(
                    "We need at least five listening artists to build your vibe."
                )
            artist_inputs = [_resolve_artist(lastfm, artist) for artist in artists[:40]]
        else:
            seed_artists = payload.artists or []
            if len(seed_artists) != 3 or any(not name.strip() for name in seed_artists):
                raise HTTPException(
                    status_code=422, detail="Seed mode needs exactly three artists."
                )
            artists = [name.strip() for name in seed_artists]
            artist_inputs = [
                _resolve_artist(lastfm, {"name": name}, equal_weight=True)
                for name in artists
            ]
        vibe = VibeResolver().resolve_profile(artist_inputs)
        signal_pct = vibe["signal_strength_pct"] * (0.7 if payload.mode == "seed" else 1)
        ranked = await ranker.rank(
            vibe["vector"], vibe["primary"], vibe["secondary"],
            top_families=vibe["top_families"],
            top_artist_names=[artist.name for artist in artist_inputs],
        )
        if ranked.get("upstream_error"):
            provider = "/".join(ranked.get("failed_providers", ["TMDB/IGDB"]))
            raise UpstreamError(provider, "catalog retrieval", "Catalog results are unavailable.")
        response = {
            "vector": vibe["vector"],
            "signal_pct": round(signal_pct, 2),
            "archetype": vibe["primary"],
            "secondary": vibe["secondary"],
            "margin": round(float(vibe["margin"]), 4),
            "top_families": vibe["top_families"][:3],
            "movies": ranked["movies"][:10],
            "games": ranked["games"][:10],
            "attribution": ATTRIBUTION,
            "degraded": bool(ranked["degraded"] or signal_pct < 30),
            "degraded_reasons": list(ranked.get("degraded_reasons", []))
            + (["Listening signal is limited."] if signal_pct < 30 else []),
        }
        cache[key] = (time.time() + ANALYZE_CACHE_SECONDS, response)
        return response
    except ConfigError:
        raise
    except (UserNotFoundError, EmptyListeningError):
        raise
    except HTTPException:
        raise
    except UpstreamError:
        raise
    except httpx.HTTPError as exc:
        raise UpstreamError(
            "upstream", "analyze", "An upstream service request failed.", type(exc).__name__
        ) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail="The analysis could not be completed.") from exc
