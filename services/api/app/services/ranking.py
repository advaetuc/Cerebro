"""Family-aware catalog retrieval and recommendation ranking."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import re
import time
from datetime import UTC, datetime
from typing import Any

from app.services.catalog.clients import (
    CatalogClients,
    CatalogResult,
    ConfigError,
    JsonDiskCache,
    build_multiquery,
    get_cache_dir,
    redact_secrets,
)
from app.services.catalog.family_profiles import (
    FAMILY_LABELS,
    FILM_PROFILES,
    GAME_PROFILES,
)
from app.services.catalog.retrieval import (
    INTENTS,
    _dedupe_games,
    _fetch_intent,
    _igdb_query_bodies,
    _items,
    _prepare,
    family_igdb_bodies,
    family_tmdb_query_groups,
)
from app.services.vibe.archetypes import ARCHETYPES, POPULATION_MEAN, POPULATION_STD
from app.services.vibe.genre_priors import DIMENSIONS

POOL_TTL_SECONDS = 24 * 60 * 60
MMR_LAMBDA = 0.75
TOP_K = 10
COMPARABLE_DIMS = ("energy", "valence", "tempo", "era", "mainstream")
DIM_INDEX = {name: DIMENSIONS.index(name) for name in COMPARABLE_DIMS}
logger = logging.getLogger("cerebro.upstream")

TMDB_MOODS: dict[str, tuple[float, float, float]] = {
    "action": (0.92, 0.60, 0.90),
    "adventure": (0.72, 0.72, 0.70),
    "animation": (0.62, 0.75, 0.72),
    "comedy": (0.65, 0.84, 0.68),
    "crime": (0.72, 0.32, 0.60),
    "documentary": (0.30, 0.52, 0.35),
    "drama": (0.45, 0.40, 0.42),
    "family": (0.55, 0.82, 0.55),
    "fantasy": (0.62, 0.70, 0.62),
    "history": (0.48, 0.55, 0.38),
    "horror": (0.82, 0.20, 0.70),
    "music": (0.70, 0.74, 0.72),
    "mystery": (0.50, 0.35, 0.50),
    "romance": (0.40, 0.78, 0.40),
    "science fiction": (0.78, 0.55, 0.84),
    "thriller": (0.82, 0.32, 0.72),
    "war": (0.94, 0.22, 0.72),
    "western": (0.58, 0.50, 0.46),
}
IGDB_MOODS: dict[str, tuple[float, float, float]] = {
    "action": (0.92, 0.64, 0.90),
    "adventure": (0.68, 0.70, 0.67),
    "arcade": (0.82, 0.84, 0.88),
    "indie": (0.50, 0.62, 0.50),
    "music": (0.73, 0.78, 0.78),
    "platform": (0.72, 0.78, 0.80),
    "puzzle": (0.38, 0.58, 0.42),
    "racing": (0.90, 0.78, 0.94),
    "role-playing (rpg)": (0.62, 0.62, 0.60),
    "shooter": (0.94, 0.44, 0.90),
    "strategy": (0.58, 0.52, 0.44),
    "4x": (0.52, 0.54, 0.38),
    "atmospheric": (0.30, 0.45, 0.34),
    "cyberpunk": (0.78, 0.40, 0.80),
    "fantasy": (0.60, 0.72, 0.56),
    "narrative": (0.38, 0.58, 0.36),
    "open world": (0.65, 0.70, 0.62),
    "retro": (0.48, 0.55, 0.28),
    "science fiction": (0.74, 0.55, 0.78),
    "turn-based strategy": (0.48, 0.53, 0.35),
    "party": (0.82, 0.86, 0.78),
    "co-operative": (0.70, 0.80, 0.67),
    "crime": (0.68, 0.35, 0.58),
    "dark": (0.72, 0.25, 0.58),
    "survival": (0.84, 0.30, 0.66),
    "abstract": (0.40, 0.45, 0.40),
    "experimental": (0.52, 0.42, 0.48),
    "historical": (0.48, 0.58, 0.35),
    "western": (0.57, 0.53, 0.42),
    "romance": (0.40, 0.78, 0.40),
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
_SOURCE_TEMPLATES = {
    "anchor_rec": (
        "For {family} listeners: recommended alongside {anchor}.",
        "{anchor} points {family} listeners toward this pick.",
    ),
    "keyword": (
        "Matches your {family} listening through its {keyword} themes.",
        "Its {keyword} themes connect with your {family} listening.",
    ),
    "genre": (
        "A {genre} pick that fits the {p1}, {p2} mood of your listening.",
        "Your {p1}, {p2} taste finds a match in this {genre} pick.",
    ),
}


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return min(high, max(low, value))


def _zscore(value: float, dim: str) -> float:
    index = DIM_INDEX[dim]
    std = POPULATION_STD[index]
    return (value - POPULATION_MEAN[index]) / std if std else 0.0


def _comparable_cosine(user: dict[str, float], title: dict[str, float]) -> float:
    left = [_zscore(user[dim], dim) for dim in COMPARABLE_DIMS]
    right = [_zscore(title[dim], dim) for dim in COMPARABLE_DIMS]
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if not left_norm or not right_norm:
        return 0.0
    return sum(a * b for a, b in zip(left, right, strict=True)) / (left_norm * right_norm)


def _genre_names(item: dict[str, Any], kind: str, id_map: dict[int | str, str]) -> set[str]:
    key = "genre_ids" if kind == "movie" else "genres"
    names: set[str] = set()
    for raw in item.get(key, []):
        value = raw.get("id") if isinstance(raw, dict) else raw
        try:
            identifier: int | str = int(value)
        except (ValueError, TypeError):
            identifier = str(value)
        names.add(id_map.get(identifier, id_map.get(str(identifier), str(value))).casefold())
    if kind == "game":
        names.update(
            str(value.get("name", "")).casefold()
            for value in item.get("themes", [])
            if isinstance(value, dict)
        )
    return names


def _catalog_maps(values: list[Any]) -> dict[int | str, str]:
    result: dict[int | str, str] = {}
    for item in values:
        if isinstance(item, dict) and item.get("id") is not None and item.get("name"):
            result[item["id"]] = str(item["name"]).casefold()
            result[str(item["id"])] = str(item["name"]).casefold()
    return result


def _year_fraction(value: Any) -> float | None:
    year_text = str(value or "")[:4]
    try:
        year = int(year_text)
    except ValueError:
        return None
    return _clamp((year - 1950) / 70)


def _title_vector(
    item: dict[str, Any],
    kind: str,
    names: set[str],
    fallback: str = "",
) -> dict[str, float]:
    moods = TMDB_MOODS if kind == "movie" else IGDB_MOODS
    mapped = [moods[name] for name in sorted(names) if name in moods]
    if mapped:
        energy = sum(row[0] for row in mapped) / len(mapped)
        valence = sum(row[1] for row in mapped) / len(mapped)
        tempo = sum(row[2] for row in mapped) / len(mapped)
    else:
        archetype = next((row for row in ARCHETYPES if row.name == fallback), ARCHETYPES[0])
        energy, valence, tempo = (archetype.centroid[index] for index in (0, 1, 5))
    if kind == "movie":
        era = _year_fraction(item.get("release_date"))
        popularity = float(item.get("vote_count", 0) or 0)
        mainstream = _clamp((math.log10(max(popularity, 1)) - 2.7) / 1.8)
    else:
        released = item.get("first_release_date")
        year = datetime.fromtimestamp(float(released), tz=UTC).year if released else None
        era = _year_fraction(year)
        count = float(item.get("total_rating_count", 0) or 0)
        mainstream = _clamp((math.log10(max(count, 1)) - 2) / 1.5)
    if era is None:
        era = 0.5
    return {
        "energy": energy,
        "valence": valence,
        "tempo": tempo,
        "era": era,
        "mainstream": mainstream,
    }


def _why(
    family_id: str,
    source: str,
    item_id: str,
    names: set[str],
    user: dict[str, float],
    title: dict[str, float],
    anchor: str | None = None,
    keyword: str | None = None,
) -> str:
    family = FAMILY_LABELS.get(family_id, family_id.replace("-", " ").title())
    chosen = int.from_bytes(hashlib.sha256(item_id.encode()).digest()[:4], "big")
    source = source if source in _SOURCE_TEMPLATES else "genre"
    templates = _SOURCE_TEMPLATES[source]
    if source == "anchor_rec":
        return templates[chosen % len(templates)].format(
            family=family, anchor=anchor or "a related title"
        )
    if source == "keyword":
        term = keyword or next(
            iter(sorted(FILM_PROFILES.get(family_id, FILM_PROFILES["pop"]).tmdb_keywords)), ""
        )
        return templates[chosen % len(templates)].format(family=family, keyword=term or "genre")
    genre = next(iter(sorted(names)), "genre")
    agreement = sorted(
        (
            (abs(_zscore(user[dim], dim)) + abs(_zscore(title[dim], dim)), dim)
            for dim in COMPARABLE_DIMS
        ),
        reverse=True,
    )
    phrases = []
    for _, dim in agreement:
        left, right = _zscore(user[dim], dim), _zscore(title[dim], dim)
        if left * right > 0:
            phrases.append(DIM_PHRASES[dim][1 if left > 0 else 0])
        if len(phrases) == 2:
            break
    for fallback_phrase in ("slow-burning", "moody"):
        if len(phrases) >= 2:
            break
        if fallback_phrase not in phrases:
            phrases.append(fallback_phrase)
    return templates[chosen % len(templates)].format(genre=genre, p1=phrases[0], p2=phrases[1])


def _explanation(user: dict[str, float], title: dict[str, float], title_id: str) -> str:
    """Retain the generic explanation helper for existing prototype checks."""
    candidates = []
    for dim in DIMENSIONS:
        left, right = _zscore(user[dim], dim), _zscore(title[dim], dim)
        if left * right > 0 and abs(left) >= 0.3 and abs(right) >= 0.3:
            candidates.append((abs(left) / (1 + abs(left - right)), dim, left))
    candidates.sort(reverse=True)
    phrases = [DIM_PHRASES[dim][1 if left > 0 else 0] for _, dim, left in candidates[:2]]
    if len(phrases) == 2:
        return f"A {phrases[0]}, {phrases[1]} pick for your listening."
    if phrases:
        return f"A {phrases[0]} pick for your listening."
    return "Close to the sound of your top genres."


def _quality(item: dict[str, Any], kind: str) -> float:
    if kind == "movie":
        rating = float(item.get("vote_average", 0) or 0)
        count = max(float(item.get("vote_count", 0) or 0), 0)
        return ((count / (count + 500)) * rating + (500 / (count + 500)) * 6.5) / 10
    rating = float(item.get("total_rating", 0) or 0) / 100
    count = max(float(item.get("total_rating_count", 0) or 0), 0)
    return (count / (count + 100)) * rating + (100 / (count + 100)) * 0.70


def _jaccard(left: set[str], right: set[str]) -> float:
    union = left | right
    return len(left & right) / len(union) if union else 0.0


def _mmr(
    candidates: list[dict[str, Any]],
    limit: int = TOP_K,
    family_order: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Select family coverage first, then diversify remaining genre sets."""
    selected: list[dict[str, Any]] = []
    remaining = candidates.copy()
    present_ids = {
        candidate.get("matched_family", {}).get("id")
        for candidate in candidates
        if candidate.get("matched_family")
    }
    ordered_ids = family_order or list(dict.fromkeys(
        candidate.get("matched_family", {}).get("id")
        for candidate in candidates
        if candidate.get("matched_family")
    ))
    quotas = {
        family_id: 2 if index == 0 else 1
        for index, family_id in enumerate(ordered_ids[:3])
        if family_id in present_ids
    }
    for family_id, quota in quotas.items():
        for _ in range(quota):
            options = [
                item for item in remaining if item.get("matched_family", {}).get("id") == family_id
            ]
            if not options or len(selected) >= limit:
                break
            pick = max(options, key=lambda item: item["score"])
            selected.append(pick)
            remaining.remove(pick)
    while remaining and len(selected) < limit:
        pick = max(
            remaining,
            key=lambda item: (
                MMR_LAMBDA * item["score"]
                - (1 - MMR_LAMBDA)
                * max(
                    (_jaccard(item["_genres"], prior["_genres"]) for prior in selected), default=0
                )
            ),
        )
        selected.append(pick)
        remaining.remove(pick)
    picks = [
        {
            key: round(value, 4) if key == "score" else value
            for key, value in item.items()
            if not key.startswith("_")
        }
        for item in selected
    ]
    scores = [item["score"] for item in picks]
    low, high = min(scores, default=0), max(scores, default=0)
    for item in picks:
        pct = (item["score"] - low) / (high - low) if high > low else 0.5
        item["match_pct"] = round(72 + pct * 25)
    return picks


class RankingService:
    """Retrieve cached family pools, score candidates, and diversify picks."""

    def __init__(
        self,
        clients: CatalogClients,
        *,
        disk_cache: JsonDiskCache | None = None,
        ttl_seconds: int = POOL_TTL_SECONDS,
    ) -> None:
        self.clients = clients
        self.disk_cache = disk_cache or JsonDiskCache(
            get_cache_dir() / "catalog" / "pools", ttl_seconds
        )
        self.ttl_seconds = ttl_seconds
        self._memory: dict[str, tuple[float, dict[str, Any]]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def _family_pool(self, family_id: str, ids: dict[str, Any]) -> dict[str, Any]:
        lock = self._locks.setdefault(f"family:{family_id}", asyncio.Lock())
        async with lock:
            now = time.time()
            cached = self._memory.get(family_id)
            if cached and cached[0] > now:
                return cached[1]
            key = f"family-pool:{family_id}"
            fresh = self.disk_cache.get(key)
            if isinstance(fresh, dict):
                self._memory[family_id] = (now + self.ttl_seconds, fresh)
                return fresh
            stale_getter = getattr(self.disk_cache, "get_stale", None)
            stale = stale_getter(key) if stale_getter else None
            try:
                pool = await self._retrieve_family(family_id, ids)
            except ConfigError:
                raise
            except Exception as exc:
                logger.warning(
                    "family retrieval failed family=%s error=%s: %s",
                    family_id,
                    type(exc).__name__,
                    redact_secrets(str(exc))[:120],
                )
                pool = {
                    "movies": [],
                    "games": [],
                    "degraded": True,
                    "error": True,
                    "movies_failed": True,
                    "games_failed": True,
                }
            stale_served = False
            if isinstance(stale, dict) and (pool.get("movies_failed") or pool.get("games_failed")):
                pool = dict(pool)
                for kind, failed in (
                    ("movies", pool.get("movies_failed")),
                    ("games", pool.get("games_failed")),
                ):
                    if failed and stale.get(kind):
                        pool[kind] = stale[kind]
                        pool.setdefault("sources", {}).update(stale.get("sources", {}))
                        pool.setdefault("anchor_names", {}).update(stale.get("anchor_names", {}))
                        stale_served = True
                if stale_served:
                    logger.warning("serving stale catalog pool family=%s", family_id)
            if not stale_served and not pool.get("error"):
                self.disk_cache.set(key, pool)
            self._memory[family_id] = (now + self.ttl_seconds, pool)
            return pool

    async def _retrieve_family(self, family_id: str, ids: dict[str, Any]) -> dict[str, Any]:
        film = FILM_PROFILES[family_id]
        game = GAME_PROFILES[family_id]
        movie_query_groups = family_tmdb_query_groups(film, ids)
        movie_tasks = [self.clients.tmdb_discover(query) for _, query in movie_query_groups]
        anchor_specs = [
            self.clients.tmdb_search_movie(*_anchor_parts(anchor)) for anchor in film.anchors
        ]
        movie_calls = await asyncio.gather(*movie_tasks, *anchor_specs, return_exceptions=True)
        movie_results = [value for value in movie_calls if isinstance(value, CatalogResult)]
        retrieval_degraded = any(not isinstance(value, CatalogResult) for value in movie_calls)
        for value in movie_calls:
            if isinstance(value, Exception):
                _log_exception("TMDB", "family movie retrieval", value)
        for result in movie_results:
            if not result.ok:
                _log_failure("TMDB", result)
        movie_items: dict[str, dict[str, Any]] = {}
        sources: dict[str, dict[str, str]] = {}
        anchor_names: dict[str, dict[str, str]] = {}
        for source, query_result in zip(
            [source for source, _ in movie_query_groups],
            movie_calls[: len(movie_tasks)],
            strict=True,
        ):
            if not isinstance(query_result, CatalogResult):
                continue
            result = query_result
            for item in _items(result, "results"):
                if item.get("id") is not None:
                    key = str(item["id"])
                    movie_items.setdefault(key, item)
                    sources.setdefault(f"movie:{key}", {})[family_id] = source
        for anchor, result in zip(film.anchors, movie_calls[len(movie_tasks) :], strict=False):
            if not isinstance(result, CatalogResult):
                continue
            matches = _items(result, "results")
            if not matches:
                continue
            anchor_id = matches[0].get("id")
            if anchor_id is None:
                continue
            recommendation = await self.clients.tmdb_movie_recommendations(int(anchor_id))
            if not recommendation.ok:
                _log_failure("TMDB", recommendation)
                retrieval_degraded = True
            for item in _items(recommendation, "results"):
                if item.get("id") is not None:
                    key = str(item["id"])
                    movie_items.setdefault(key, item)
                    source_key = f"movie:{key}"
                    sources.setdefault(source_key, {})[family_id] = "anchor_rec"
                    anchor_names.setdefault(source_key, {})[family_id] = anchor.rsplit(" (", 1)[0]
        game_bodies = family_igdb_bodies(game, ids)
        game_results = await asyncio.gather(
            *(self.clients.igdb_games(body) for body in game_bodies), return_exceptions=True
        )
        retrieval_degraded = retrieval_degraded or any(
            not isinstance(result, CatalogResult) for result in game_results
        )
        for result in game_results:
            if isinstance(result, Exception):
                _log_exception("IGDB", "games", result)
        for result in game_results:
            if isinstance(result, CatalogResult) and not result.ok:
                _log_failure("IGDB", result)
        game_items: dict[str, dict[str, Any]] = {}
        for result in game_results:
            if not isinstance(result, CatalogResult):
                continue
            for item in _items(result):
                if item.get("id") is not None:
                    key = str(item["id"])
                    game_items.setdefault(key, item)
                    source = "genre"
                    family_sources = sources.setdefault(f"game:{key}", {})
                    if family_id not in family_sources:
                        family_sources[family_id] = source
        for anchor in game.anchors:
            search = await self.clients.igdb_search_game(anchor)
            if not search.ok:
                _log_failure("IGDB", search)
                retrieval_degraded = True
            for record in _items(search):
                similar = record.get("similar_games", [])
                if not isinstance(similar, list):
                    continue
                result = await self.clients.igdb_games_by_ids(
                    [int(value) for value in similar[:40]]
                )
                if not result.ok:
                    _log_failure("IGDB", result)
                    retrieval_degraded = True
                for item in _items(result):
                    if item.get("id") is not None:
                        key = str(item["id"])
                        game_items.setdefault(key, item)
                        source_key = f"game:{key}"
                        sources.setdefault(source_key, {})[family_id] = "anchor_rec"
                        anchor_names.setdefault(source_key, {})[family_id] = anchor
        failed_movies = not any(result.ok for result in movie_results)
        failed_games = not any(
            isinstance(result, CatalogResult) and result.ok for result in game_results
        )
        unresolved_keywords = [
            name
            for name in film.tmdb_keywords
            if ids.get("tmdb_keywords", {}).get(name.casefold()) is None
        ]
        keyword_reasons = ids.get("unresolved_keywords", {})
        degraded_reasons = [
            f"TMDB keyword {name}: {keyword_reasons.get(name, 'no_match')}"
            for name in unresolved_keywords
        ]
        return {
            "movies": list(movie_items.values()),
            "games": list(game_items.values()),
            "sources": sources,
            "anchor_names": anchor_names,
            "error": failed_movies and failed_games,
            "movies_failed": failed_movies,
            "games_failed": failed_games,
            "degraded_reasons": degraded_reasons,
            "degraded": retrieval_degraded
            or bool(degraded_reasons)
            or any(not item.ok for item in movie_results)
            or any(not isinstance(item, CatalogResult) or not item.ok for item in game_results),
        }

    async def _pools_and_ids(
        self, primary: str, secondary: str
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        """Retain compatibility for tests and development tools using archetype pools."""
        ids = await _prepare(self.clients)
        pools = await asyncio.gather(
            self._archetype_pool(primary, ids), self._archetype_pool(secondary, ids)
        )
        return pools[0], pools[1], ids

    async def _archetype_pool(self, archetype: str, ids: dict[str, Any]) -> dict[str, Any]:
        key = f"legacy-pool:{archetype}"
        cached = self.disk_cache.get(key)
        if isinstance(cached, dict):
            return cached
        intent = INTENTS[archetype]
        movie = await _fetch_intent(self.clients, intent, ids, False)
        bodies = _igdb_query_bodies(intent, ids)
        result = await self.clients.igdb_multiquery(build_multiquery(bodies))
        pool = {
            "movies": movie["items"],
            "games": _dedupe_games([result]) if result.ok else [],
            "degraded": movie["degraded"] or not result.ok,
            "movies_failed": not any(call.ok for call in movie["calls"]),
            "games_failed": not result.ok,
            "degraded_reasons": [],
        }
        self.disk_cache.set(key, pool)
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
        """Return top recommendations based on the user's strongest families."""
        ids = await _prepare(self.clients)
        for result in ids.get("boot_results", []):
            if hasattr(result, "ok") and not result.ok and "search/keyword:" not in result.endpoint:
                provider = "TMDB" if result.endpoint.startswith(("genre/", "search/")) else "IGDB"
                _log_failure(provider, result)
        families = [item for item in (top_families or []) if float(item.get("share", 0)) >= 0.08][
            :4
        ]
        if not families:
            families = [{"id": family, "share": 1 / 3} for family in list(FILM_PROFILES)[:3]]
        family_ids = [str(item["id"]) for item in families if item.get("id") in FILM_PROFILES]
        pools = await asyncio.gather(*(self._family_pool(fid, ids) for fid in family_ids))
        configuration = await self.clients.tmdb_configuration()
        if not configuration.ok:
            _log_failure("TMDB", configuration)
        images = (
            configuration.data.get("images", {})
            if configuration.ok and isinstance(configuration.data, dict)
            else {}
        )
        image_base = str(images.get("secure_base_url", "https://image.tmdb.org/t/p/"))
        tmdb_map = _catalog_maps(tmdb_genres or _dict_list(ids.get("tmdb_genres", {})))
        igdb_map = _catalog_maps(igdb_genres or _dict_list(ids.get("igdb_genres", {})))
        theme_map = _catalog_maps(igdb_themes or _dict_list(ids.get("igdb_themes", {})))
        movie_rows = _merge_family_items(pools, family_ids, "movies")
        game_rows = _merge_family_items(pools, family_ids, "games")
        if len(movie_rows) < 30 or len(game_rows) < 30:
            fallback = await asyncio.gather(
                self._archetype_pool(primary, ids), self._archetype_pool(secondary, ids)
            )
            for kind, rows in (("movies", movie_rows), ("games", game_rows)):
                if len(rows) < 30:
                    existing = {str(item["item"].get("id")) for item in rows}
                    for pool in fallback:
                        for item in pool[kind]:
                            if str(item.get("id")) not in existing:
                                rows.append({"item": item, "sources": {}, "shares": {}})
                                existing.add(str(item.get("id")))
        movies = [
            _score_candidate(row, "movie", vector, primary, families, tmdb_map, {}, {}, image_base)
            for row in movie_rows
        ]
        games = [
            _score_candidate(
                row, "game", vector, primary, families, tmdb_map, igdb_map, theme_map, image_base
            )
            for row in game_rows
        ]
        unavailable_movie = not movie_rows
        unavailable_game = not game_rows
        degraded_reasons = {reason for pool in pools for reason in pool.get("degraded_reasons", [])}
        if unavailable_movie:
            degraded_reasons.add("movies unavailable")
        if unavailable_game:
            degraded_reasons.add("games unavailable")
        if not configuration.ok:
            degraded_reasons.add("TMDB image configuration unavailable")
        return {
            "movies": _mmr(movies, family_order=family_ids),
            "games": _mmr(games, family_order=family_ids),
            "degraded": any(pool.get("degraded") for pool in pools) or not configuration.ok,
            "upstream_error": unavailable_movie and unavailable_game,
            "failed_providers": [
                provider
                for provider, failed in (("TMDB", unavailable_movie), ("IGDB", unavailable_game))
                if failed
            ],
            "degraded_reasons": sorted(degraded_reasons),
        }


def _dict_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, dict):
        return []
    return [
        {"id": identifier, "name": name}
        for name, identifier in value.items()
        if isinstance(identifier, int)
    ]


def _merge_family_items(
    pools: list[dict[str, Any]], family_ids: list[str], key: str
) -> list[dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for family_id, pool in zip(family_ids, pools, strict=True):
        for item in pool.get(key, []):
            item_id = str(item.get("id", ""))
            if not item_id:
                continue
            row = found.setdefault(
                item_id, {"item": item, "sources": {}, "shares": {}, "anchors": {}}
            )
            source_key = f"{'movie' if key == 'movies' else 'game'}:{item_id}"
            row["sources"][family_id] = (
                pool.get("sources", {}).get(source_key, {}).get(family_id, "genre")
            )
            row["anchors"][family_id] = (
                pool.get("anchor_names", {}).get(source_key, {}).get(family_id)
            )
    return list(found.values())


def _score_candidate(
    row: dict[str, Any],
    kind: str,
    user: dict[str, float],
    fallback: str,
    families: list[dict[str, Any]],
    tmdb_map: dict[Any, str],
    igdb_map: dict[Any, str],
    theme_map: dict[Any, str],
    image_base: str,
) -> dict[str, Any]:
    item = row["item"]
    names = _genre_names(item, kind, tmdb_map if kind == "movie" else igdb_map)
    if kind == "game":
        names.update(_genre_names({"themes": item.get("themes", [])}, "game", theme_map))
    title = _title_vector(item, kind, names, fallback)
    sources = row.get("sources", {})
    shares = {str(family["id"]): float(family.get("share", 0)) for family in families}
    denominator = sum(shares.values()) or 1
    affinity = (
        sum(
            shares[family_id] * _source_match(source, family_id)
            for family_id, source in sources.items()
            if family_id in shares
        )
        / denominator
    )
    vibe = (_comparable_cosine(user, title) + 1) / 2
    quality = _quality(item, kind)
    fit = 0.5 * (1 - abs(user["era"] - title["era"])) + 0.5 * (
        1 - abs(user["mainstream"] - title["mainstream"])
    )
    score = 0.45 * affinity + 0.20 * vibe + 0.20 * quality + 0.15 * fit
    matched_id = max(
        sources,
        key=lambda family_id: (
            shares.get(family_id, 0) * _source_match(sources[family_id], family_id)
        ),
        default=None,
    )
    matched_id = matched_id or (next(iter(shares)) if shares else "pop")
    matched_source = sources.get(matched_id, "genre")
    anchor = row.get("anchors", {}).get(matched_id)
    why = _why(
        matched_id,
        matched_source,
        str(item.get("id", "")),
        names,
        user,
        title,
        anchor,
        next(iter(sorted(names)), None) if kind == "game" else None,
    )
    if kind == "movie":
        title_text = str(item.get("title", ""))
        year = str(item.get("release_date", ""))[:4] or None
        poster = item.get("poster_path")
        image = f"{image_base}w342{poster}" if poster else None
        source_url = f"https://www.themoviedb.org/movie/{item.get('id')}"
    else:
        title_text = str(item.get("name", ""))
        released = item.get("first_release_date")
        year = str(datetime.fromtimestamp(float(released), tz=UTC).year) if released else None
        cover = item.get("cover")
        cover_id = cover.get("image_id") if isinstance(cover, dict) else None
        image = (
            f"https://images.igdb.com/igdb/image/upload/t_cover_big/{cover_id}.jpg"
            if cover_id
            else None
        )
        source_url = f"https://www.igdb.com/games/{item.get('id')}"
    return {
        "id": str(item.get("id", "")),
        "title": title_text,
        "year": year,
        "image_url": image,
        "score": score,
        "why": why,
        "source_url": source_url,
        "matched_family": {"id": matched_id, "label": FAMILY_LABELS.get(matched_id, matched_id)},
        "reason_source": matched_source,
        "_genres": names,
    }


def _source_match(source: str, family_id: str) -> float:
    if source == "anchor_rec":
        return 1.0
    if source == "keyword":
        return 0.7
    if source in {"language", "genre_language"}:
        return 0.5
    profile = FILM_PROFILES.get(family_id)
    return 0.5 if profile and profile.original_languages else 0.3


def _log_failure(provider: str, result: CatalogResult) -> None:
    """Log an upstream catalog error without exposing configured credentials."""
    detail = result.error_detail or result.error_message or f"HTTP {result.status}"
    detail = redact_secrets(str(detail))
    error_type, separator, message = detail.partition(":")
    if not separator:
        error_type, message = "UpstreamError", detail
    logger.warning(
        "upstream failure provider=%s endpoint=%s exception=%s message=%s",
        provider,
        result.endpoint,
        error_type[:60],
        message.strip()[:120],
    )


def _log_exception(provider: str, endpoint: str, error: Exception) -> None:
    """Log an upstream transport exception safely."""
    logger.warning(
        "upstream failure provider=%s endpoint=%s exception=%s message=%s",
        provider,
        endpoint,
        type(error).__name__[:60],
        redact_secrets(str(error))[:120],
    )


def _anchor_parts(anchor: str) -> tuple[str, str | None]:
    match = re.match(r"^(.*?)\s*\((\d{4})\)$", anchor)
    return (match.group(1), match.group(2)) if match else (anchor, None)
