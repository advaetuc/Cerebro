"""FastAPI entry point for the no-UI Cerebro prototype."""

from __future__ import annotations

import json
import sys
import time
from typing import Any

import httpx
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.services.catalog.clients import (
    CatalogClients,
    ConfigError,
    build_headers,
    clean_env_value,
)
from app.services.ranking import RankingService
from app.services.vibe import ArtistInput, VibeResolver
from spike.lastfm_client import LastFmClient, LastFmResult

ANALYZE_CACHE_SECONDS = 15 * 60
ATTRIBUTION = [
    "Data provided by Last.fm",
    "This product uses the TMDB API but is not endorsed or certified by TMDB.",
    "IGDB data provided by Twitch Interactive, Inc.",
]
JUNK_TAGS = frozenset(
    {"seen live", "favorites", "favourite", "favourites", "albums i own", "spotify"}
)

app = FastAPI(title="Cerebro API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


class AnalyzeRequest(BaseModel):
    """Accept either a Last.fm username or exactly three seed artists."""

    mode: str
    username: str | None = None
    artists: list[str] | None = None


class UpstreamError(RuntimeError):
    """Represent an unsuccessful upstream service response."""


@app.on_event("startup")
async def startup() -> None:
    """Initialize shared clients and preserve clear startup configuration errors."""
    load_dotenv(encoding="utf-8-sig")
    app.state.config_error = None
    app.state.analyze_cache = {}
    app.state.lastfm = None
    app.state.catalog = None
    app.state.ranker = None
    try:
        tmdb_token = clean_env_value("TMDB_READ_TOKEN")
        client_id = clean_env_value("TWITCH_CLIENT_ID")
        if tmdb_token:
            build_headers({"Authorization": ("TMDB_READ_TOKEN", f"Bearer {tmdb_token}")})
        if client_id:
            build_headers({"Client-ID": ("TWITCH_CLIENT_ID", client_id)})
        app.state.catalog = CatalogClients()
        app.state.ranker = RankingService(app.state.catalog)
    except ConfigError as exc:
        app.state.config_error = str(exc)
        print(str(exc), file=sys.stderr)
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


@app.exception_handler(UpstreamError)
async def upstream_error_handler(request: Request, exc: UpstreamError) -> JSONResponse:
    return JSONResponse(status_code=502, content={"detail": str(exc)})


@app.exception_handler(httpx.HTTPError)
async def http_error_handler(request: Request, exc: httpx.HTTPError) -> JSONResponse:
    return JSONResponse(status_code=502, content={"detail": "An upstream service request failed."})


# @app.exception_handler(Exception)
# async def unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
#     return JSONResponse(status_code=502, content={"detail": "The request could not be completed."})


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


def _require_success(result: LastFmResult) -> None:
    if not result.ok:
        raise UpstreamError("Last.fm could not complete the request.")


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


def _tag_payload(result: LastFmResult) -> list[dict[str, Any]]:
    if not result.ok:
        raise UpstreamError("Last.fm could not load artist tags.")
    return _items(result, "toptags", "tag")


def _top_artists(client: LastFmClient, username: str) -> list[dict[str, Any]]:
    combined: dict[str, dict[str, Any]] = {}
    for period in ("6month", "overall"):
        result = client.user_top_artists(username, period, limit=50)
        _require_success(result)
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
        raise UpstreamError("Last.fm artist search failed.") from exc


def _analyze_key(payload: AnalyzeRequest) -> str:
    return json.dumps(payload.dict(), sort_keys=True, separators=(",", ":"))


@app.post("/analyze")
async def analyze(
    payload: AnalyzeRequest,
    request: Request,
    lastfm: LastFmClient = Depends(get_lastfm_client),  # noqa: B008
    ranker: RankingService = Depends(get_ranking_service),  # noqa: B008
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
            if not username:
                raise HTTPException(status_code=422, detail="Enter a Last.fm username.")
            artists = _top_artists(lastfm, username)
            if len(artists) < 5:
                raise HTTPException(
                    status_code=422,
                    detail="We need at least five listening artists to build your vibe.",
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
            vibe["vector"], vibe["primary"], vibe["secondary"]
        )
        if ranked.get("upstream_error"):
            raise UpstreamError("Catalog providers could not complete the request.")
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
        }
        cache[key] = (time.time() + ANALYZE_CACHE_SECONDS, response)
        return response
    except ConfigError:
        raise
    except HTTPException:
        raise
    except UpstreamError:
        raise
    except httpx.HTTPError as exc:
        raise UpstreamError("An upstream service request failed.") from exc
    except Exception as exc:
        # raise UpstreamError("The analysis could not be completed.") from exc
        import traceback
        traceback.print_exc() # Forces the real stack trace to print to your Uvicorn window!
        raise HTTPException(status_code=500, detail=f"Debug Error: {type(exc).__name__} - {str(exc)}")
