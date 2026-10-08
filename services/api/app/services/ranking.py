"""Small in-process recommendation ranker for the prototype API."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import time
from pathlib import Path
from typing import Any

from app.services.catalog.clients import (
    CatalogClients,
    CatalogResult,
    ConfigError,
    JsonDiskCache,
    build_multiquery,
    redact_secrets,
)
from app.services.catalog.retrieval import (
    INTENTS,
    _dedupe_games,
    _fetch_intent,
    _igdb_query_bodies,
    _prepare,
)
from app.services.vibe.archetypes import ARCHETYPES, _standardize
from app.services.vibe.genre_priors import DIMENSIONS, FAMILY_BY_ID

POOL_TTL_SECONDS = 24 * 60 * 60
POOL_CACHE_DIR = Path(__file__).resolve().parents[2] / "spike" / ".cache" / "catalog" / "pools"
MMR_LAMBDA = 0.7
TOP_K = 10
REGIONAL_FAMILIES = {
    "hindi-film", "punjabi-pop", "haryanvi", "desi-hip-hop", "indian-indie"
}
logger = logging.getLogger("cerebro.upstream")

TMDB_GENRE_FAMILIES = {
    "action": "hard-rock",
    "adventure": "folk-rock",
    "animation": "k-pop",
    "comedy": "disco-dance",
    "crime": "boom-bap",
    "documentary": "experimental",
    "drama": "singer-songwriter",
    "family": "pop",
    "fantasy": "world",
    "history": "classical",
    "horror": "metal",
    "music": "disco-dance",
    "mystery": "idm",
    "romance": "r-and-b-soul",
    "science fiction": "electropop",
    "thriller": "downtempo-trip-hop",
    "war": "metal",
    "western": "americana",
}
IGDB_GENRE_FAMILIES = {
    "action": "hard-rock",
    "adventure": "folk-rock",
    "arcade": "disco-dance",
    "indie": "indian-indie",
    "music": "disco-dance",
    "platform": "pop",
    "puzzle": "idm",
    "racing": "electropop",
    "role-playing (rpg)": "world",
    "shooter": "hip-hop",
    "strategy": "boom-bap",
}
IGDB_THEME_FAMILIES = {
    "4x": "boom-bap",
    "action": "hard-rock",
    "atmospheric": "ambient-chillout",
    "cyberpunk": "electropop",
    "fantasy": "world",
    "indie": "indian-indie",
    "narrative": "singer-songwriter",
    "open world": "folk-rock",
    "retro": "classic-rock",
    "science fiction": "idm",
    "turn-based strategy": "boom-bap",
}
TMDB_GENRE_CENTROIDS = {
    genre: FAMILY_BY_ID[family].dims for genre, family in TMDB_GENRE_FAMILIES.items()
}
IGDB_GENRE_CENTROIDS = {
    genre: FAMILY_BY_ID[family].dims for genre, family in IGDB_GENRE_FAMILIES.items()
}
IGDB_THEME_CENTROIDS = {
    theme: FAMILY_BY_ID[family].dims for theme, family in IGDB_THEME_FAMILIES.items()
}
DIM_PHRASES = {
    "energy": ("slow-burning", "high-energy"),
    "valence": ("moody", "upbeat"),
    "acousticness": ("polished and electronic", "warm and acoustic"),
    "danceability": ("headphone-first", "groove-driven"),
    "instrumentalness": ("vocal-led", "instrumental"),
    "tempo": ("unhurried", "fast-paced"),
    "era": ("classic", "modern"),
    "mainstream": ("left-of-center", "crowd-pleasing"),
}
WHY_TEMPLATES = (
    "A {p1}, {p2} pick for your listening.",
    "Echoes the {p1} and {p2} side of your taste.",
    "Lines up with your {p1}, {p2} playlists.",
    "Fits the {p1}, {p2} mood you keep coming back to.",
)
WHY_ONE_DIM_TEMPLATES = (
    "A {phrase} pick for your listening.",
    "Echoes the {phrase} side of your taste.",
    "Lines up with your {phrase} playlists.",
    "Fits the {phrase} mood you keep coming back to.",
)


def _cosine(left: dict[str, float], right: dict[str, float]) -> float:
    left_values = _standardize(tuple(left[dim] for dim in DIMENSIONS))
    right_values = _standardize(tuple(right[dim] for dim in DIMENSIONS))
    dot = sum(a * b for a, b in zip(left_values, right_values, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left_values))
    right_norm = math.sqrt(sum(value * value for value in right_values))
    return dot / (left_norm * right_norm) if left_norm and right_norm else 0.0


def _id_name_map(values: list[Any]) -> dict[int | str, str]:
    """Safely map raw genre configurations into a standard lookup dictionary."""
    mapping = {}
    if not isinstance(values, list):
        return mapping

    for item in values:
        # Case A: The item is a flat string (e.g., 'action')
        if isinstance(item, str):
            cleaned_item = item.strip().casefold()
            mapping[cleaned_item] = cleaned_item
            # Try parsing it as a pure numeric ID string if possible
            try:
                mapping[int(cleaned_item)] = cleaned_item
            except ValueError:
                pass
            continue
            
        # Case B: The item is a standard dictionary (e.g., {"id": 28, "name": "Action"})
        if isinstance(item, dict):
            item_id = item.get("id")
            item_name = item.get("name")
            if item_id is not None and item_name:
                cleaned_name = str(item_name).casefold()
                mapping[item_id] = cleaned_name
                # Ensure integer string representations work as numeric index keys too
                try:
                    mapping[int(item_id)] = cleaned_name
                except (ValueError, TypeError):
                    pass
                try:
                    mapping[str(item_id)] = cleaned_name
                except (ValueError, TypeError):
                    pass

    return mapping



def _title_vector(
    item: dict[str, Any],
    kind: str,
    archetype: str,
    tmdb_genres: dict[int, str],
    igdb_genres: dict[int, str],
    igdb_themes: dict[int, str],
) -> dict[str, float]:
    vectors: list[tuple[float, ...]] = []
    if kind == "movie":
        vectors = [
            TMDB_GENRE_CENTROIDS[name]
            for genre_id in item.get("genre_ids", [])
            if (name := tmdb_genres.get(int(genre_id))) in TMDB_GENRE_CENTROIDS
        ]
    else:
        vectors = [
            IGDB_GENRE_CENTROIDS[name]
            for genre_id in item.get("genres", [])
            if (name := igdb_genres.get(int(genre_id))) in IGDB_GENRE_CENTROIDS
        ] + [
            IGDB_THEME_CENTROIDS[name]
            for theme_id in item.get("themes", [])
            if (name := igdb_themes.get(int(theme_id))) in IGDB_THEME_CENTROIDS
        ]
    if not vectors:
        return dict(zip(
            DIMENSIONS,
            next(entry.centroid for entry in ARCHETYPES if entry.name == archetype),
            strict=True,
        ))
    return {
        dim: sum(vector[index] for vector in vectors) / len(vectors)
        for index, dim in enumerate(DIMENSIONS)
    }


def _genre_set(item: dict[str, Any], kind: str, names: dict[int, str]) -> set[str]:
    key = "genre_ids" if kind == "movie" else "genres"
    return {names.get(int(value), str(value)) for value in item.get(key, [])}


def _jaccard(left: set[str], right: set[str]) -> float:
    union = left | right
    return len(left & right) / len(union) if union else 0.0


def _safe_error_text(value: str) -> str:
    return redact_secrets(value)[:160]


def _log_catalog_failure(provider: str, result: Any) -> str:
    detail = getattr(result, "error_detail", None) or getattr(result, "error_message", None)
    detail = detail or f"HTTP {getattr(result, 'status', 'unknown')}"
    detail = _safe_error_text(str(detail))
    exception_name, _, message = detail.partition(":")
    if not message:
        exception_name, message = "UpstreamError", detail
    endpoint = str(getattr(result, "endpoint", "unknown"))
    logger.warning(
        "upstream failure provider=%s endpoint=%s exception=%s message=%s",
        provider,
        endpoint,
        exception_name[:60],
        message.strip()[:120],
    )
    return detail


def _explanation(user: dict[str, float], title: dict[str, float], title_id: str) -> str:
    user_z = _standardize(tuple(user[dim] for dim in DIMENSIONS))
    title_z = _standardize(tuple(title[dim] for dim in DIMENSIONS))
    candidates = []
    for index, dim in enumerate(DIMENSIONS):
        left = user_z[index]
        right = title_z[index]
        if left == 0 or right == 0 or (left > 0) != (right > 0):
            continue
        if abs(left) < 0.3 or abs(right) < 0.3:
            continue
        agreement = abs(left) / (1 + abs(left - right))
        candidates.append((agreement, index, dim))
    candidates.sort(reverse=True)
    phrases = [
        DIM_PHRASES[dim][1 if user_z[index] >= 0 else 0]
        for _, index, dim in candidates[:2]
    ]
    chosen = int.from_bytes(hashlib.sha256(title_id.encode()).digest()[:4], "big")
    template_index = chosen % len(WHY_TEMPLATES)
    if len(phrases) >= 2:
        return WHY_TEMPLATES[template_index].format(p1=phrases[0], p2=phrases[1])
    if phrases:
        return WHY_ONE_DIM_TEMPLATES[template_index].format(phrase=phrases[0])
    return "Close to the sound of your top genres."


def _candidate(
    item: dict[str, Any],
    kind: str,
    weight: float,
    user: dict[str, float],
    archetype: str,
    tmdb_genres: dict[int, str],
    igdb_genres: dict[int, str],
    igdb_themes: dict[int, str],
    tmdb_image_base: str,
) -> dict[str, Any]:
    vector = _title_vector(
        item, kind, archetype, tmdb_genres, igdb_genres, igdb_themes
    )
    quality = (
        float(item.get("vote_average", 0) or 0) / 10
        if kind == "movie"
        else float(item.get("total_rating", 0) or 0) / 100
    )
    novelty = 1.0 - abs(user["mainstream"] - vector["mainstream"])
    raw_score = 0.60 * _cosine(user, vector) + 0.30 * quality + 0.10 * novelty
    score = raw_score * weight
    ids = _genre_set(item, kind, tmdb_genres if kind == "movie" else igdb_genres)
    if kind == "movie":
        title = str(item.get("title", ""))
        year = str(item.get("release_date", ""))[:4] or None
        poster = item.get("poster_path")
        image_url = f"{tmdb_image_base}w342{poster}" if poster else None
        source_url = f"https://www.themoviedb.org/movie/{item.get('id')}"
    else:
        title = str(item.get("name", ""))
        released = item.get("first_release_date")
        year = time.strftime("%Y", time.gmtime(released)) if released else None
        cover = item.get("cover")
        image_id = cover.get("image_id") if isinstance(cover, dict) else None
        image_url = (
            f"https://images.igdb.com/igdb/image/upload/t_cover_big/{image_id}.jpg"
            if image_id
            else None
        )
        source_url = f"https://www.igdb.com/games/{item.get('id')}"
    return {
        "id": str(item.get("id", "")),
        "title": title,
        "year": year,
        "image_url": image_url,
        "score": score,
        "why": _explanation(user, vector, str(item.get("id", ""))),
        "source_url": source_url,
        "_genres": ids,
        "_vector": vector,
        "_weight": weight,
    }


def _mmr(candidates: list[dict[str, Any]], limit: int = TOP_K) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    remaining = candidates.copy()
    regional_count = 0
    while remaining and len(selected) < limit:
        eligible = [
            item for item in remaining
            if not item.get("regional") or regional_count < 3
        ]
        if not eligible:
            break
        best = max(
            eligible,
            key=lambda candidate: MMR_LAMBDA * candidate["score"]
            - (1 - MMR_LAMBDA) * max(
                (_jaccard(candidate["_genres"], prior["_genres"]) for prior in selected),
                default=0.0,
            ),
        )
        remaining.remove(best)
        selected.append(best)
        regional_count += int(bool(best.get("regional")))
    picks = [
        {
            key: round(value, 4) if key == "score" else value
            for key, value in item.items()
            if not key.startswith("_")
        }
        for item in selected
    ]
    scores = [float(item["score"]) for item in picks]
    minimum = min(scores, default=0.0)
    maximum = max(scores, default=0.0)
    span = maximum - minimum
    for item in picks:
        normalized = (float(item["score"]) - minimum) / span if span else 0.5
        item["match_pct"] = round(72 + normalized * 25)
    return picks


class RankingService:
    """Retrieve archetype candidate pools and rank them for one vibe vector."""

    def __init__(
        self,
        clients: CatalogClients,
        *,
        disk_cache: JsonDiskCache | None = None,
        ttl_seconds: int = POOL_TTL_SECONDS,
    ) -> None:
        self.clients = clients
        self.disk_cache = disk_cache or JsonDiskCache(POOL_CACHE_DIR, ttl_seconds)
        self.ttl_seconds = ttl_seconds
        self._memory: dict[str, tuple[float, dict[str, Any]]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def _pool(self, archetype: str, ids: dict[str, Any]) -> dict[str, Any]:
        lock = self._locks.setdefault(archetype, asyncio.Lock())
        async with lock:
            return await self._load_pool(archetype, ids)

    async def _load_pool(self, archetype: str, ids: dict[str, Any]) -> dict[str, Any]:
        now = time.time()
        if archetype in self._memory and self._memory[archetype][0] > now:
            return self._memory[archetype][1]
        key = f"pool:{archetype}"
        cached = self.disk_cache.get(key)
        if isinstance(cached, dict):
            self._memory[archetype] = (now + self.ttl_seconds, cached)
            return cached
        stale_getter = getattr(self.disk_cache, "get_stale", None)
        stale = stale_getter(key) if stale_getter else None
        intent = INTENTS[archetype]
        try:
            movies = await _fetch_intent(self.clients, intent, ids, False)
        except ConfigError:
            raise
        except Exception as exc:
            detail = f"{type(exc).__name__}: {_safe_error_text(str(exc))}"
            movies = {
                "items": [],
                "calls": [
                    CatalogResult("discover/movie", None, None, "transport_error", detail, detail)
                ],
                "unresolved_keywords": list(intent["tmdb_keywords"]),
                "keyword_errors": {name: type(exc).__name__ for name in intent["tmdb_keywords"]},
                "degraded": True,
            }
        bodies = _igdb_query_bodies(intent, ids)
        has_igdb_intents = any(ids.get("igdb_genres", {}).values()) or any(
            ids.get("igdb_themes", {}).values()
        )
        if ids.get("igdb_available") and has_igdb_intents:
            try:
                igdb_result = await self.clients.igdb_multiquery(build_multiquery(bodies))
            except ConfigError:
                raise
            except Exception as exc:
                detail = f"{type(exc).__name__}: {_safe_error_text(str(exc))}"
                igdb_result = CatalogResult(
                    "multiquery", None, None, "transport_error", detail, detail
                )
        else:
            igdb_result = CatalogResult(
                "multiquery", None, None, "configuration_error",
                "No IGDB genre or theme IDs were available.",
                "ConfigError: No IGDB genre or theme IDs were available.",
            )
        failed_movie_calls = [call for call in movies["calls"] if not call.ok]
        failed_game_call = not igdb_result.ok
        for call in movies["calls"]:
            if not call.ok:
                _log_catalog_failure("TMDB", call)
        if failed_game_call:
            _log_catalog_failure("IGDB", igdb_result)
        movie_items = movies["items"]
        game_items = _dedupe_games([igdb_result]) if igdb_result.ok else []
        used_stale_movies = bool(failed_movie_calls and stale and stale.get("movies"))
        used_stale_games = bool(failed_game_call and stale and stale.get("games"))
        if used_stale_movies:
            movie_items = stale["movies"]
            logger.warning("serving stale catalog pool provider=TMDB endpoint=discover/movie")
        if used_stale_games:
            game_items = stale["games"]
            logger.warning("serving stale catalog pool provider=IGDB endpoint=multiquery")
        movies_failed = bool(failed_movie_calls) and not used_stale_movies and not any(
            call.ok for call in movies["calls"]
        )
        games_failed = failed_game_call and not used_stale_games
        keyword_reasons = {
            name: movies.get("keyword_errors", {}).get(name, "no_match")
            for name in movies.get("unresolved_keywords", [])
        }
        pool = {
            "movies": movie_items,
            "games": game_items,
            "movies_failed": movies_failed,
            "games_failed": games_failed,
            "degraded": bool(failed_movie_calls or failed_game_call or movies["degraded"]),
            "upstream_error": movies_failed and games_failed,
            "unresolved_keywords": movies["unresolved_keywords"],
            "keyword_errors": keyword_reasons,
            "degraded_reasons": [
                f"TMDB keyword {name}: {reason}" for name, reason in keyword_reasons.items()
            ],
            "provider_errors": [
                {"provider": "TMDB", "endpoint": call.endpoint,
                 "detail": call.error_detail or call.error_message or f"HTTP {call.status}"}
                for call in failed_movie_calls
            ] + ([{"provider": "IGDB", "endpoint": igdb_result.endpoint,
                   "detail": igdb_result.error_detail or igdb_result.error_message or
                   f"HTTP {igdb_result.status}"}] if failed_game_call else []),
        }
        self.disk_cache.set(key, pool)
        self._memory[archetype] = (now + self.ttl_seconds, pool)
        return pool

    async def rank(
        self,
        vector: dict[str, float],
        primary: str,
        secondary: str,
        *,
        tmdb_genres: list[dict[str, Any]] | None = None,
        igdb_genres: list[dict[str, Any]] | None = None,
        igdb_themes: list[dict[str, Any]] | None = None,
        top_families: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Return diversified movie and game recommendations from two pools."""
        primary_pool, secondary_pool, ids = await self._pools_and_ids(primary, secondary)
        for result in ids.get("boot_results", []):
            if (hasattr(result, "ok") and not result.ok
                    and "keyword:" not in result.endpoint
                    and not result.endpoint.startswith("search/keyword")):
                provider = "TMDB" if result.endpoint.startswith(("genre/", "search/")) else "IGDB"
                _log_catalog_failure(provider, result)
        try:
            configuration = await self.clients.tmdb_configuration()
        except ConfigError:
            raise
        except Exception as exc:
            detail = f"{type(exc).__name__}: {_safe_error_text(str(exc))}"
            configuration = CatalogResult(
                "configuration", None, None, "transport_error", detail, detail
            )
        if not configuration.ok:
            _log_catalog_failure("TMDB", configuration)
        images = configuration.data.get("images", {}) if configuration.ok else {}
        tmdb_image_base = (
            str(images.get("secure_base_url", "https://image.tmdb.org/t/p/"))
            if isinstance(images, dict)
            else "https://image.tmdb.org/t/p/"
        )
        tmdb_map = _id_name_map(tmdb_genres or ids["tmdb_genres"])
        igdb_map = _id_name_map(igdb_genres or ids["igdb_genres"])
        theme_map = _id_name_map(igdb_themes or ids["igdb_themes"])
        movies = self._weighted_candidates(
            primary_pool["movies"], secondary_pool["movies"], "movie", vector,
            primary, secondary, tmdb_map, igdb_map, theme_map,
            tmdb_image_base,
        )
        games = self._weighted_candidates(
            primary_pool["games"], secondary_pool["games"], "game", vector,
            primary, secondary, tmdb_map, igdb_map, theme_map,
            tmdb_image_base,
        )
        regional_share = sum(
            float(family.get("share", 0))
            for family in (top_families or [])
            if family.get("id") in REGIONAL_FAMILIES
        )
        regional_degraded = False
        regional_unavailable = False
        regional_items: list[dict[str, Any]] = []
        if regional_share >= 0.25:
            regional_items, regional_degraded, regional_unavailable = await self._regional_pool()
            existing_ids = {str(item.get("id")) for item in movies}
            for item in regional_items:
                if str(item.get("id")) in existing_ids:
                    continue
                candidate = _candidate(
                    item, "movie", 1.0, vector, primary,
                    tmdb_map, igdb_map, theme_map, tmdb_image_base,
                )
                candidate["regional"] = True
                movies.append(candidate)
        movies_unavailable = all(
            pool.get("movies_failed", False) for pool in (primary_pool, secondary_pool)
        ) and not regional_items
        games_unavailable = all(
            pool.get("games_failed", False) for pool in (primary_pool, secondary_pool)
        )
        degraded_reasons = sorted({
            reason
            for pool in (primary_pool, secondary_pool)
            for reason in pool.get("degraded_reasons", [])
        })
        if movies_unavailable:
            degraded_reasons.append("movies unavailable")
        if games_unavailable:
            degraded_reasons.append("games unavailable")
        if regional_unavailable:
            degraded_reasons.append("regional movies unavailable")
        degraded = any(pool.get("degraded", False) for pool in (primary_pool, secondary_pool))
        degraded = degraded or regional_degraded or not configuration.ok
        return {
            "movies": [] if movies_unavailable else _mmr(movies),
            "games": [] if games_unavailable else _mmr(games),
            "degraded": degraded,
            "upstream_error": movies_unavailable and games_unavailable,
            "failed_providers": [
                provider
                for provider, failed in (("TMDB", movies_unavailable), ("IGDB", games_unavailable))
                if failed
            ],
            "degraded_reasons": degraded_reasons,
        }

    async def _regional_pool(self) -> tuple[list[dict[str, Any]], bool, bool]:
        key = "regional:movies:hi-pa-ta-te"
        fresh = self.disk_cache.get(key)
        if isinstance(fresh, list):
            return fresh, False, False
        stale_getter = getattr(self.disk_cache, "get_stale", None)
        stale = stale_getter(key) if stale_getter else None
        queries = [
            {
                "include_adult": "false",
                "sort_by": "popularity.desc",
                "vote_count.gte": 500,
                "vote_average.gte": 6,
                "with_original_language": "hi|pa|ta|te",
                "page": page,
            }
            for page in (1, 2)
        ]
        calls = await asyncio.gather(*(
            self.clients.tmdb_discover(query) for query in queries
        ), return_exceptions=True)
        valid: list[CatalogResult] = []
        failed: list[CatalogResult] = []
        for call in calls:
            if isinstance(call, Exception):
                if isinstance(call, ConfigError):
                    raise call
                detail = f"{type(call).__name__}: {_safe_error_text(str(call))}"
                call = CatalogResult(
                    "discover/movie", None, None, "transport_error", detail, detail
                )
            if call.ok:
                valid.append(call)
            else:
                failed.append(call)
                _log_catalog_failure("TMDB", call)
        found = []
        for call in valid:
            response = call.data.get("results", []) if isinstance(call.data, dict) else []
            if isinstance(response, list):
                found.extend(item for item in response if isinstance(item, dict))
        deduped = list({
            str(item["id"]): item for item in reversed(found) if item.get("id") is not None
        }.values())
        if failed and not valid and isinstance(stale, list):
            logger.warning("serving stale catalog pool provider=TMDB endpoint=regional-movies")
            return stale, True, False
        if valid:
            self.disk_cache.set(key, deduped)
        return deduped, bool(failed), bool(failed and not valid and stale is None)

    async def _pools_and_ids(
        self, primary: str, secondary: str
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        ids = await _prepare(self.clients)
        primary_pool, secondary_pool = await asyncio.gather(
            self._pool(primary, ids), self._pool(secondary, ids)
        )
        return primary_pool, secondary_pool, ids

    @staticmethod
    def _weighted_candidates(
        primary_items: list[dict[str, Any]],
        secondary_items: list[dict[str, Any]],
        kind: str,
        vector: dict[str, float],
        primary: str,
        secondary: str,
        tmdb_map: dict[int, str],
        igdb_map: dict[int, str],
        theme_map: dict[int, str],
        tmdb_image_base: str,
    ) -> list[dict[str, Any]]:
        combined: dict[str, tuple[dict[str, Any], float, str]] = {}
        for item in primary_items:
            combined[str(item.get("id"))] = (item, 0.7, primary)
        for item in secondary_items:
            key = str(item.get("id"))
            if key in combined:
                existing_item, existing_weight, existing_archetype = combined[key]
                combined[key] = (existing_item, existing_weight + 0.3, existing_archetype)
            else:
                combined[key] = (item, 0.3, secondary)
        return [
            _candidate(
                item, kind, weight, vector, archetype,
                tmdb_map, igdb_map, theme_map, tmdb_image_base,
            )
            for item, weight, archetype in combined.values()
        ]
