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
    exact_igdb_anchor_matches,
    family_igdb_bodies,
    family_tmdb_query_groups,
    match_tmdb_anchor_result,
    normalize_catalog_name,
    parse_anchor,
)
from app.services.vibe.archetypes import ARCHETYPES, POPULATION_MEAN, POPULATION_STD
from app.services.vibe.genre_priors import DIMENSIONS

POOL_TTL_SECONDS = 24 * 60 * 60
POOL_CACHE_VERSION = "v2"
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
    "anchor": ("A touchstone of {family} on screen.", "A defining {family} touchstone on screen."),
    "anchor_rec": (
        "For {family} listeners: recommended alongside {anchor}.",
        "{anchor} points {family} listeners toward this pick.",
    ),
    "keyword": (
        "Matches your {family} listening through its {keyword} themes.",
        "Its {keyword} themes connect with your {family} listening.",
    ),
    "language_genre": (
        "Popular {language} cinema for {family} listeners.",
        "A {language} film selection for {family} listeners.",
    ),
    "genre": (
        "{article}{genre} pick that fits the {p1}, {p2} mood of your listening.",
        "Your {p1}, {p2} taste finds a match in this {genre} pick.",
    ),
    "borrowed_anchor": (
        "Popular with fans of {borrowed_family} and similar music.",
        "Fans of {borrowed_family} and similar music often enjoy this pick.",
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


def _eligible_movie(
    item: dict[str, Any],
    languages: tuple[str, ...],
    excludes: set[Any],
    genres: set[Any],
    vote_floor: int,
    require_genre: bool = True,
) -> bool:
    if int(item.get("vote_count", 0) or 0) < vote_floor:
        return False
    if languages and item.get("original_language") not in languages:
        return False
    item_genres = set(item.get("genre_ids", []))
    return not item_genres.intersection(excludes) and (
        not require_genre or bool(item_genres.intersection(genres))
    )


def _eligible_anchor_rec(
    item: dict[str, Any], languages: tuple[str, ...], excludes: set[Any], genres: set[Any]
) -> bool:
    """Keep anchor recommendations inside the family's film mood and language."""
    if float(item.get("vote_average", 0) or 0) < 6.3:
        return False
    if languages and item.get("original_language") not in languages:
        return False
    item_genres = set(item.get("genre_ids", []))
    return bool(item_genres.intersection(genres)) and not item_genres.intersection(excludes)


def _set_source(sources: dict[str, dict[str, str]], key: str, family_id: str, source: str) -> None:
    family_sources = sources.setdefault(key, {})
    current = family_sources.get(family_id)
    if current is None or _SOURCE_PRIORITY.get(source, 0) > _SOURCE_PRIORITY.get(current, 0):
        family_sources[family_id] = source


_SOURCE_PRIORITY = {
    "genre": 1,
    "anchor_rec": 2,
    "keyword": 3,
    "language_genre": 4,
    "borrowed_anchor": 5,
    "anchor": 6,
}

BORROWED_GAME_FAMILIES = {
    "punjabi-pop": "hip-hop",
    "haryanvi": "hip-hop",
    "desi-hip-hop": "hip-hop",
    "hindi-film": "pop",
    "indian-indie": "alt-indie-rock",
}


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
    kind: str = "movie",
    language: str | None = None,
    borrowed_from: str | None = None,
) -> str:
    family = FAMILY_LABELS.get(family_id, family_id.replace("-", " ").title())
    chosen = int.from_bytes(hashlib.sha256(item_id.encode()).digest()[:4], "big")
    source = source if source in _SOURCE_TEMPLATES else "genre"
    templates = _SOURCE_TEMPLATES[source]
    if source == "anchor":
        template = templates[chosen % len(templates)].format(family=family)
        return template.replace("on screen", "in games") if kind == "game" else template
    if source == "language_genre":
        language_label = {"hi": "Hindi", "pa": "Punjabi", "ko": "Korean"}.get(
            language or "", "regional"
        )
        return templates[chosen % len(templates)].format(family=family, language=language_label)
    if source == "anchor_rec":
        return templates[chosen % len(templates)].format(
            family=family, anchor=anchor or "a related title"
        )
    if source == "borrowed_anchor":
        borrowed_family = FAMILY_LABELS.get(
            borrowed_from or "hip-hop", (borrowed_from or "hip-hop").replace("-", " ").title()
        )
        return templates[chosen % len(templates)].format(borrowed_family=borrowed_family)
    if source == "keyword":
        term = keyword or next(
            iter(sorted(FILM_PROFILES.get(family_id, FILM_PROFILES["pop"]).tmdb_keywords)), ""
        )
        return templates[chosen % len(templates)].format(family=family, keyword=term or "genre")
    genre = next(iter(sorted(names)), "genre")
    article = _article(genre)
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
        if left * right > 0 and abs(left) >= 0.3 and abs(right) >= 0.3:
            phrases.append(DIM_PHRASES[dim][1 if left > 0 else 0])
        if len(phrases) == 2:
            break
    if not phrases:
        return "Close to the sound of your top genres."
    if len(phrases) == 1:
        phrases.append(phrases[0])
    return templates[chosen % len(templates)].format(
        article=article, genre=genre, p1=phrases[0], p2=phrases[1]
    )


def _article(value: str) -> str:
    """Return the correct indefinite article for a short genre phrase."""
    return "An " if value[:1].casefold() in "aeiou" else "A "


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
    return _quality_with_prior(item, kind, 500 if kind == "movie" else 100)


def _quality_with_prior(item: dict[str, Any], kind: str, shrinkage: int) -> float:
    if kind == "movie":
        rating = float(item.get("vote_average", 0) or 0)
        count = max(float(item.get("vote_count", 0) or 0), 0)
        return (
            (count / (count + shrinkage)) * rating + (shrinkage / (count + shrinkage)) * 6.5
        ) / 10
    rating = float(item.get("total_rating", 0) or 0) / 100
    count = max(float(item.get("total_rating_count", 0) or 0), 0)
    prior = 0.70
    return (count / (count + shrinkage)) * rating + (shrinkage / (count + shrinkage)) * prior


def _jaccard(left: set[str], right: set[str]) -> float:
    union = left | right
    return len(left & right) / len(union) if union else 0.0


def _mmr(
    candidates: list[dict[str, Any]],
    limit: int = TOP_K,
    family_order: list[str] | None = None,
    kind: str = "movie",
    top_family: str | None = None,
) -> list[dict[str, Any]]:
    """Select family coverage first, then diversify remaining genre sets."""
    selected: list[dict[str, Any]] = []
    remaining = candidates.copy()
    present_ids = {
        candidate.get("matched_family", {}).get("id")
        for candidate in candidates
        if candidate.get("matched_family")
    }
    family_rows = (
        family_order
        if family_order and isinstance(family_order[0], dict)
        else [
            {"id": family_id, "share": 1 / max(len(present_ids), 1)}
            for family_id in (family_order or list(present_ids))
        ]
    )
    ordered_ids = [str(row["id"]) for row in family_rows] or list(
        dict.fromkeys(
            candidate.get("matched_family", {}).get("id")
            for candidate in candidates
            if candidate.get("matched_family")
        )
    )
    quotas = _allocate_family_slots(candidates, family_rows, limit)
    family_shares = {str(item["id"]): float(item.get("share", 0)) for item in family_rows}
    for family_id in ordered_ids:
        quota = quotas.get(family_id, 0)
        if quota <= 0 or family_shares.get(family_id, 0) < 0.15:
            continue
        anchors = [
            item
            for item in remaining
            if item.get("matched_family", {}).get("id") == family_id
            and item.get("reason_source") in {"anchor", "borrowed_anchor"}
        ]
        required = 2 if quota >= 4 else 1
        for pick in sorted(anchors, key=lambda item: item["score"], reverse=True)[:required]:
            if len(selected) >= limit:
                break
            selected.append(pick)
            remaining.remove(pick)
    for family_id, quota in quotas.items():
        current = sum(item.get("matched_family", {}).get("id") == family_id for item in selected)
        for _ in range(max(0, quota - current)):
            options = [
                item for item in remaining if item.get("matched_family", {}).get("id") == family_id
            ]
            profile = FILM_PROFILES.get(family_id) if kind == "movie" else None
            if profile and profile.original_languages:
                required = math.ceil(quota * 0.6)
                language_picks = sum(
                    item.get("matched_family", {}).get("id") == family_id
                    and item.get("reason_source") == "language_genre"
                    for item in selected
                )
                if language_picks < required:
                    language_options = [
                        item
                        for item in options
                        if item.get("_original_language") in profile.original_languages
                    ]
                    if language_options:
                        options = language_options
            if not options or len(selected) >= limit:
                break
            options = _selection_filtered(options, selected, kind, top_family)
            if not options:
                break
            pick = max(options, key=lambda item: item["score"])
            selected.append(pick)
            remaining.remove(pick)
    while remaining and len(selected) < limit:
        options = _selection_filtered(remaining, selected, kind, top_family)
        if not options:
            break
        pick = max(
            options,
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


def _allocate_family_slots(
    candidates: list[dict[str, Any]], family_order: list[dict[str, Any]] | None, limit: int
) -> dict[str, int]:
    """Allocate family quotas proportionally with largest remainders."""
    if not family_order or limit <= 0:
        return {}
    available = {
        str(row.get("id")): sum(
            item.get("matched_family", {}).get("id") == row.get("id") for item in candidates
        )
        for row in family_order
        if float(row.get("share", 0)) >= 0.05
    }
    weights = {
        str(row["id"]): max(0.0, float(row.get("share", 0)))
        for row in family_order
        if str(row.get("id")) in available and available[str(row["id"])] > 0
    }
    denominator = sum(weights.values())
    if not denominator:
        return {}
    exact = {family: limit * weight / denominator for family, weight in weights.items()}
    quotas = {family: min(available[family], math.floor(value)) for family, value in exact.items()}
    remainders = sorted(
        weights,
        key=lambda family: (-(exact[family] - math.floor(exact[family])), family),
    )
    for family in remainders:
        if sum(quotas.values()) >= limit:
            break
        if quotas[family] < available[family]:
            quotas[family] += 1
    for row in family_order:
        family = str(row.get("id"))
        share = float(row.get("share", 0))
        if family not in quotas:
            continue
        if share >= 0.15:
            quotas[family] = max(1, quotas[family])
        elif 0.05 <= share <= 0.10:
            quotas[family] = min(1, quotas[family])
    while sum(quotas.values()) > limit:
        removable = [family for family, quota in quotas.items() if quota > 1]
        if not removable:
            break
        family = min(removable, key=lambda item: weights[item])
        quotas[family] -= 1
    return quotas


def _selection_filtered(
    items: list[dict[str, Any]], selected: list[dict[str, Any]], kind: str, top_family: str | None
) -> list[dict[str, Any]]:
    candidates = items
    anchor_rec_count = sum(item.get("reason_source") == "anchor_rec" for item in selected)
    if anchor_rec_count >= 2:
        candidates = [item for item in candidates if item.get("reason_source") != "anchor_rec"]
    if kind == "movie":
        if top_family not in {"pop", "k-pop", "electropop", "disco-dance", "soundtrack"}:
            animated = sum(bool(item.get("_animation_family")) for item in selected)
            if animated >= 2:
                candidates = [item for item in candidates if not item.get("_animation_family")]
        genre_only = sum(item.get("reason_source") == "genre" for item in selected)
        if genre_only >= 2:
            candidates = [item for item in candidates if item.get("reason_source") != "genre"]
        language_candidates = []
        for item in candidates:
            family_id = item.get("matched_family", {}).get("id")
            profile = FILM_PROFILES.get(family_id)
            if not profile or not profile.original_languages:
                language_candidates.append(item)
                continue
            family_selected = [
                row for row in selected if row.get("matched_family", {}).get("id") == family_id
            ]
            matching_pool = [
                row
                for row in items
                if row.get("matched_family", {}).get("id") == family_id
                and row.get("_original_language") in profile.original_languages
            ]
            is_language = item.get("_original_language") in profile.original_languages
            if (
                len(matching_pool) > len(family_selected)
                and (
                    sum(
                        row.get("_original_language") in profile.original_languages
                        for row in family_selected
                    )
                    + int(is_language)
                )
                / (len(family_selected) + 1)
                >= 0.6
            ):
                language_candidates.append(item)
            elif not matching_pool:
                language_candidates.append(item)
        candidates = language_candidates
    else:
        genre_only = sum(item.get("reason_source") == "genre" for item in selected)
        if genre_only >= 2:
            candidates = [item for item in candidates if item.get("reason_source") != "genre"]
    return candidates


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

    async def _family_pool(
        self, family_id: str, ids: dict[str, Any], anchor_seed: bytes | None = None
    ) -> dict[str, Any]:
        anchor_seed = anchor_seed or b"default"
        anchor_key = hashlib.sha256(anchor_seed).hexdigest()[:10]
        cache_key = f"{POOL_CACHE_VERSION}:{family_id}:{anchor_key}"
        lock = self._locks.setdefault(f"family:{cache_key}", asyncio.Lock())
        async with lock:
            now = time.time()
            cached = self._memory.get(cache_key)
            if cached and cached[0] > now:
                return cached[1]
            key = f"family-pool:{cache_key}"
            fresh = self.disk_cache.get(key)
            if isinstance(fresh, dict):
                self._memory[cache_key] = (now + self.ttl_seconds, fresh)
                return fresh
            stale_getter = getattr(self.disk_cache, "get_stale", None)
            stale = stale_getter(key) if stale_getter else None
            try:
                pool = await self._retrieve_family(family_id, ids, anchor_seed)
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
                        pool.setdefault("borrowed_names", {}).update(
                            stale.get("borrowed_names", {})
                        )
                        stale_served = True
                if stale_served:
                    logger.warning("serving stale catalog pool family=%s", family_id)
            if not stale_served and not pool.get("error"):
                self.disk_cache.set(key, pool)
            self._memory[cache_key] = (now + self.ttl_seconds, pool)
            return pool

    async def _retrieve_family(
        self, family_id: str, ids: dict[str, Any], anchor_seed: bytes | None = None
    ) -> dict[str, Any]:
        film = FILM_PROFILES[family_id]
        game = GAME_PROFILES[family_id]
        anchor_seed = anchor_seed or b"default"
        film_anchors = _rotated_anchors(film.anchors, anchor_seed)
        planned_anchors, borrowed_from = _game_anchor_plan(family_id, game.anchors)
        game_anchors = _rotated_anchors(planned_anchors, anchor_seed)
        movie_query_groups = family_tmdb_query_groups(film, ids)
        movie_tasks = [self.clients.tmdb_discover(query) for _, query in movie_query_groups]
        movie_calls = await asyncio.gather(*movie_tasks, return_exceptions=True)
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
        genre_names = {name.casefold() for name in film.tmdb_genres}
        genre_ids = {ids.get("tmdb_genres", {}).get(name) for name in genre_names}
        exclude_ids = {
            ids.get("tmdb_genres", {}).get(name.casefold()) for name in film.exclude_genres
        }
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
                    if source == "language_genre" and not _eligible_movie(
                        item, film.original_languages, exclude_ids, genre_ids, 100
                    ):
                        continue
                    if source in {"genre", "keyword"} and not _eligible_movie(
                        item, (), exclude_ids, genre_ids, 500 if source == "keyword" else 300
                    ):
                        continue
                    key = str(item["id"])
                    movie_items.setdefault(key, item)
                    _set_source(sources, f"movie:{key}", family_id, source)
        unresolved_anchors: list[str] = []
        for anchor in film_anchors:
            title, year = parse_anchor(anchor)
            anchor_search = await self.clients.tmdb_search_movie(title)
            anchor_item = match_tmdb_anchor_result(anchor_search, title, year)
            if not anchor_search.ok:
                _log_failure("TMDB", anchor_search)
                retrieval_degraded = True
            if not anchor_item:
                unresolved_anchors.append(anchor)
                continue
            anchor_id = anchor_item.get("id")
            if anchor_id is not None:
                key = str(anchor_id)
                movie_items[key] = anchor_item
                _set_source(sources, f"movie:{key}", family_id, "anchor")
                anchor_names.setdefault(f"movie:{key}", {})[family_id] = anchor.split(" (", 1)[0]
                recommendation = await self.clients.tmdb_movie_recommendations(int(anchor_id))
                if not recommendation.ok:
                    _log_failure("TMDB", recommendation)
                    retrieval_degraded = True
                for item in _items(recommendation, "results"):
                    if not _eligible_anchor_rec(
                        item, film.original_languages, exclude_ids, genre_ids
                    ):
                        continue
                    key = str(item["id"])
                    movie_items.setdefault(key, item)
                    source_key = f"movie:{key}"
                    _set_source(sources, source_key, family_id, "anchor_rec")
                    anchor_names.setdefault(source_key, {})[family_id] = anchor.split(" (", 1)[0]
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
        borrowed_names: dict[str, str] = {}
        game_theme_ids = {
            ids.get("igdb_themes", {}).get(name.casefold()) for name in game.igdb_themes
        }
        for result in game_results:
            if not isinstance(result, CatalogResult):
                continue
            for item in _items(result):
                if item.get("id") is not None:
                    key = str(item["id"])
                    item_themes = {
                        value.get("id")
                        for value in item.get("themes", [])
                        if isinstance(value, dict)
                    }
                    if not item_themes.intersection(game_theme_ids):
                        continue
                    game_items.setdefault(key, item)
                    source = "genre"
                    _set_source(sources, f"game:{key}", family_id, source)
        for anchor in game_anchors:
            search = await self.clients.igdb_search_game(anchor)
            if not search.ok:
                _log_failure("IGDB", search)
                retrieval_degraded = True
            search_matches = exact_igdb_anchor_matches(search, anchor)
            candidate = None
            if search_matches:
                detail = await self.clients.igdb_games_by_ids(
                    [int(search_matches[0]["id"])],
                    rating_count_floor=None,
                    rating_floor=None,
                )
                if not detail.ok:
                    _log_failure("IGDB", detail)
                    retrieval_degraded = True
                else:
                    candidate = next(iter(_items(detail)), None)
            if not candidate:
                unresolved_anchors.append(anchor)
                continue
            candidate_id = candidate.get("id")
            if candidate_id is not None:
                key = str(candidate_id)
                game_items[key] = candidate
                _set_source(
                    sources,
                    f"game:{key}",
                    family_id,
                    "borrowed_anchor" if borrowed_from else "anchor",
                )
                if borrowed_from:
                    borrowed_names[key] = borrowed_from
            similar = search_matches[0].get("similar_games", []) if search_matches else []
            if similar:
                result = await self.clients.igdb_games_by_ids(
                    [int(value) for value in similar[:40]], rating_count_floor=50, rating_floor=None
                )
                if not result.ok:
                    _log_failure("IGDB", result)
                    retrieval_degraded = True
                for item in _items(result):
                    if item.get("id") is None or int(item.get("total_rating_count", 0) or 0) < 50:
                        continue
                    key = str(item["id"])
                    game_items.setdefault(key, item)
                    _set_source(
                        sources,
                        f"game:{key}",
                        family_id,
                        "borrowed_anchor" if borrowed_from else "anchor_rec",
                    )
                    anchor_names.setdefault(f"game:{key}", {})[family_id] = anchor
                    if borrowed_from:
                        borrowed_names[key] = borrowed_from
        failed_movies = not movie_items and not any(result.ok for result in movie_results)
        failed_games = not game_items and not any(
            isinstance(result, CatalogResult) and result.ok for result in game_results
        )
        degraded_reasons = [f"Unresolved anchor: {name}" for name in unresolved_anchors]
        keyword_errors = ids.get("unresolved_keywords", {})
        degraded_reasons.extend(
            f"TMDB keyword {name}: {error}"
            for name in film.tmdb_keywords
            if (error := keyword_errors.get(name))
        )
        return {
            "movies": list(movie_items.values()),
            "games": list(game_items.values()),
            "sources": sources,
            "anchor_names": anchor_names,
            "borrowed_names": borrowed_names,
            "keywords": {
                family_id: next(
                    (
                        name
                        for name in film.tmdb_keywords
                        if ids.get("tmdb_keywords", {}).get(name.casefold()) is not None
                    ),
                    None,
                )
            },
            "unresolved_anchors": unresolved_anchors,
            "error": failed_movies and failed_games,
            "movies_failed": failed_movies,
            "games_failed": failed_games,
            "degraded_reasons": degraded_reasons,
            "degraded": retrieval_degraded
            or bool(keyword_errors)
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
        top_artist_names: list[str] | None = None,
    ) -> dict[str, Any]:
        """Return top recommendations based on the user's strongest families."""
        ids = await _prepare(self.clients)
        families = [item for item in (top_families or []) if float(item.get("share", 0)) >= 0.08][
            :4
        ]
        if not families:
            families = [{"id": family, "share": 1 / 3} for family in list(FILM_PROFILES)[:3]]
        family_ids = [str(item["id"]) for item in families if item.get("id") in FILM_PROFILES]
        artist_hash = hashlib.sha256("|".join(sorted(top_artist_names or [])).encode()).digest()
        pools = await asyncio.gather(
            *(self._family_pool(fid, ids, artist_hash) for fid in family_ids)
        )
        anchor_rec_families = {
            family_ids[index]
            for index, family in enumerate(families[:2])
            if index < len(family_ids) and float(family.get("share", 0)) >= 0.15
        }
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
        movie_rows = _merge_family_items(
            pools, family_ids, "movies", anchor_rec_families=anchor_rec_families
        )
        game_rows = _merge_family_items(pools, family_ids, "games")
        game_rows = _dedupe_game_rows(game_rows)
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
            "movies": _mmr(
                movies,
                family_order=families,
                kind="movie",
                top_family=family_ids[0] if family_ids else None,
            ),
            "games": _mmr(
                games,
                family_order=families,
                kind="game",
                top_family=family_ids[0] if family_ids else None,
            ),
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


def _rotated_anchors(anchors: tuple[str, ...], seed: bytes) -> list[str]:
    if not anchors:
        return []
    offset = int.from_bytes(seed[:4], "big") % len(anchors)
    return list((anchors[offset:] + anchors[:offset])[:4])


def _game_anchor_plan(
    family_id: str, anchors: tuple[str, ...]
) -> tuple[tuple[str, ...], str | None]:
    """Return own game anchors or the configured borrowed family anchors."""
    if anchors:
        return anchors, None
    borrowed_from = BORROWED_GAME_FAMILIES.get(family_id)
    if borrowed_from:
        return GAME_PROFILES[borrowed_from].anchors, borrowed_from
    return (), None


def _merge_family_items(
    pools: list[dict[str, Any]],
    family_ids: list[str],
    key: str,
    *,
    anchor_rec_families: set[str] | None = None,
) -> list[dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for family_id, pool in zip(family_ids, pools, strict=True):
        for item in pool.get(key, []):
            item_id = str(item.get("id", ""))
            if not item_id:
                continue
            row = found.setdefault(
                item_id,
                {"item": item, "sources": {}, "shares": {}, "anchors": {}, "keywords": {}},
            )
            source_key = f"{'movie' if key == 'movies' else 'game'}:{item_id}"
            source = pool.get("sources", {}).get(source_key, {}).get(family_id, "genre")
            if source == "anchor_rec" and family_id not in (anchor_rec_families or set()):
                if not row["sources"]:
                    found.pop(item_id, None)
                continue
            current = row["sources"].get(family_id)
            if current is None or _SOURCE_PRIORITY.get(source, 0) > _SOURCE_PRIORITY.get(
                current, 0
            ):
                row["sources"][family_id] = source
            anchor = pool.get("anchor_names", {}).get(source_key, {}).get(family_id)
            if anchor:
                row["anchors"][family_id] = anchor
            borrowed = pool.get("borrowed_names", {}).get(item_id)
            if borrowed:
                row.setdefault("borrowed_from", {})[family_id] = borrowed
            row["keywords"][family_id] = pool.get("keywords", {}).get(family_id)
    return list(found.values())


def _dedupe_game_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse normalized-name ports, retaining popularity and earliest year."""
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        item = row["item"]
        normalized = normalize_catalog_name(str(item.get("name", "")))
        key = normalized or str(item.get("id", ""))
        current = grouped.get(key)
        if current is None:
            grouped[key] = row
            continue
        old_item = current["item"]
        old_count = int(old_item.get("total_rating_count", 0) or 0)
        new_count = int(item.get("total_rating_count", 0) or 0)
        dates = [
            float(value)
            for value in (old_item.get("first_release_date"), item.get("first_release_date"))
            if value
        ]
        winner = row if new_count > old_count else current
        winner["item"] = dict(winner["item"])
        if dates:
            winner["item"]["first_release_date"] = min(dates)
        for field in ("sources", "shares", "anchors", "keywords", "borrowed_from"):
            combined = dict(current.get(field, {}))
            combined.update(row.get(field, {}))
            winner[field] = combined
        grouped[key] = winner
    return list(grouped.values())


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
    matched_id = max(
        sources,
        key=lambda family_id: (
            shares.get(family_id, 0) * _source_match(sources[family_id], family_id)
        ),
        default=None,
    )
    matched_id = matched_id or (next(iter(shares)) if shares else "pop")
    matched_source = sources.get(matched_id, "genre")
    profile = FILM_PROFILES.get(matched_id) if kind == "movie" else None
    shrinkage = (
        150
        if profile and profile.original_languages and profile.anchors
        else (500 if kind == "movie" else 100)
    )
    quality = _quality_with_prior(item, kind, shrinkage)
    fit = 0.5 * (1 - abs(user["era"] - title["era"])) + 0.5 * (
        1 - abs(user["mainstream"] - title["mainstream"])
    )
    if matched_source == "anchor":
        fit = 1.0
        if kind == "movie":
            quality = float(item.get("vote_average", 0) or 0) / 10
    score = 0.45 * affinity + 0.20 * vibe + 0.20 * quality + 0.15 * fit
    anchor = row.get("anchors", {}).get(matched_id)
    why = _why(
        matched_id,
        matched_source,
        str(item.get("id", "")),
        names,
        user,
        title,
        anchor,
        row.get("keywords", {}).get(matched_id)
        or (next(iter(sorted(names)), None) if kind == "game" else None),
        kind=kind,
        language=item.get("original_language"),
        borrowed_from=row.get("borrowed_from", {}).get(matched_id),
    )
    if kind == "movie":
        title_text = str(item.get("title", ""))
        year = str(item.get("release_date", ""))[:4] or None
        poster = item.get("poster_path")
        image = f"{image_base}w342{poster}" if poster else None
        source_url = f"https://www.themoviedb.org/movie/{item.get('id')}"
        original_language = item.get("original_language")
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
        original_language = None
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
        "original_language": original_language,
        "_components": {
            "affinity": affinity,
            "vibe": vibe,
            "quality": quality,
            "fit": fit,
            "score": score,
        },
        "_original_language": original_language,
        "_animation_family": bool(names.intersection({"animation", "family"})),
        "_genres": names,
    }


def _source_match(source: str, family_id: str) -> float:
    if source == "anchor":
        return 1.0
    if source == "anchor_rec":
        return 0.35
    if source == "borrowed_anchor":
        return 0.6
    if source == "keyword":
        return 0.6
    if source == "language_genre":
        return 0.8
    if source == "genre":
        return 0.2
    return 0.0


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
