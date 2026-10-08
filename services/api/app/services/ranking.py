"""Small in-process recommendation ranker for the prototype API."""

from __future__ import annotations

import asyncio
import hashlib
import math
import time
from pathlib import Path
from typing import Any

from app.services.catalog.clients import CatalogClients, JsonDiskCache, build_multiquery
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
    "valence": ("moody", "uplifting"),
    "acousticness": ("electrically textured", "warm and acoustic"),
    "danceability": ("made for listening in", "built to move to"),
    "instrumentalness": ("vocal-led", "instrumental"),
    "tempo": ("unhurried", "fast-paced"),
    "era": ("classic", "modern"),
    "mainstream": ("off the beaten path", "widely loved"),
}
WHY_TEMPLATES = (
    "Its {phrases} qualities line up with your listening profile.",
    "You may connect with its {phrases} feel.",
    "This pick reflects your taste for {phrases} sounds.",
    "Your profile points toward its {phrases} character.",
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


def _explanation(user: dict[str, float], title: dict[str, float], title_id: str) -> str:
    user_z = _standardize(tuple(user[dim] for dim in DIMENSIONS))
    title_z = _standardize(tuple(title[dim] for dim in DIMENSIONS))
    candidates = []
    for index, dim in enumerate(DIMENSIONS):
        magnitude = abs(user_z[index])
        if dim in {"instrumentalness", "era"} and magnitude <= 1:
            continue
        agreement = 1 / (1 + abs(user_z[index] - title_z[index]))
        candidates.append((agreement * magnitude, index, dim))
    candidates.sort(reverse=True)
    phrases = [
        DIM_PHRASES[dim][1 if user_z[index] >= 0 else 0]
        for _, index, dim in candidates[:2]
    ]
    chosen = int.from_bytes(hashlib.sha256(title_id.encode()).digest()[:4], "big")
    return WHY_TEMPLATES[chosen % len(WHY_TEMPLATES)].format(
        phrases=" and ".join(phrases) if phrases else "distinctive"
    )


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
    match_pct = round(max(60, min(98, 60 + (raw_score - 0.35) * 95)))
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
        "match_pct": match_pct,
        "why": _explanation(user, vector, str(item.get("id", ""))),
        "source_url": source_url,
        "_genres": ids,
        "_vector": vector,
        "_weight": weight,
    }


def _mmr(candidates: list[dict[str, Any]], limit: int = TOP_K) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    remaining = candidates.copy()
    while remaining and len(selected) < limit:
        best = max(
            remaining,
            key=lambda candidate: MMR_LAMBDA * candidate["score"]
            - (1 - MMR_LAMBDA) * max(
                (_jaccard(candidate["_genres"], prior["_genres"]) for prior in selected),
                default=0.0,
            ),
        )
        remaining.remove(best)
        selected.append(best)
    return [
        {
            key: round(value, 4) if key == "score" else value
            for key, value in item.items()
            if not key.startswith("_")
        }
        for item in selected
    ]


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
        intent = INTENTS[archetype]
        movies = await _fetch_intent(self.clients, intent, ids, False)
        bodies = _igdb_query_bodies(intent, ids)
        igdb_result = await self.clients.igdb_multiquery(build_multiquery(bodies))
        pool = {
            "movies": movies["items"],
            "games": _dedupe_games([igdb_result]),
            "degraded": movies["degraded"] or not igdb_result.ok,
            "upstream_error": (
                not any(call.ok for call in movies["calls"]) and not igdb_result.ok
            ),
            "unresolved_keywords": movies["unresolved_keywords"],
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
    ) -> dict[str, Any]:
        """Return diversified movie and game recommendations from two pools."""
        primary_pool, secondary_pool, ids = await self._pools_and_ids(primary, secondary)
        configuration = await self.clients.tmdb_configuration()
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
        degraded_reasons = sorted({
            f"Unresolved TMDB keyword: {keyword}"
            for pool in (primary_pool, secondary_pool)
            for keyword in pool.get("unresolved_keywords", [])
        })
        has_degradation = (
            primary_pool["degraded"] or secondary_pool["degraded"] or not configuration.ok
        )
        if has_degradation and not degraded_reasons:
            degraded_reasons.append("One or more catalog signals were unavailable.")
        return {
            "movies": _mmr(movies),
            "games": _mmr(games),
            "degraded": (
                primary_pool["degraded"]
                or secondary_pool["degraded"]
                or not configuration.ok
            ),
            "upstream_error": (
                primary_pool.get("upstream_error", False)
                or secondary_pool.get("upstream_error", False)
            ),
            "degraded_reasons": degraded_reasons,
        }

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
