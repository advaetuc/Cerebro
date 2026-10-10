"""Measure cached and cold TMDB and IGDB candidate retrieval."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import statistics
import time
import unicodedata
from pathlib import Path
from typing import Any

from .clients import (
    CatalogClients,
    CatalogResult,
    ConfigError,
    build_apicalypse,
    build_multiquery,
    build_tmdb_params,
    redact_secrets,
)
from .family_profiles import FILM_PROFILES

SPIKE_DIR = Path(__file__).resolve().parents[3] / "spike"
BLUEPRINT_PATH = SPIKE_DIR.parents[2] / "docs" / "cerebro_blueprint.md"
REPORT_PATH = SPIKE_DIR / "out" / "catalog_report.json"
logger = logging.getLogger("cerebro.upstream")
_NO_MATCH_LOGGED: set[str] = set()
_BOOT_FAILURE_LOGGED: set[tuple[str, str, str]] = set()

INTENTS: dict[str, dict[str, Any]] = {
    "Neon Insomniac": {
        "tmdb_genres": ["Science Fiction", "Thriller"],
        "tmdb_keywords": ["cyberpunk", "neo-noir", "rhythm"],
        "igdb_genres": ["Shooter", "Role-playing (RPG)"],
        "igdb_themes": ["Action", "Science fiction", "Cyberpunk"],
    },
    "Golden Hour Dreamer": {
        "tmdb_genres": ["Drama", "Romance"],
        "tmdb_keywords": ["coming of age", "road movie", "romance"],
        "igdb_genres": ["Adventure", "Indie"],
        "igdb_themes": ["Fantasy", "Open world", "Narrative"],
    },
    "Static Saint": {
        "tmdb_genres": ["Drama", "Science Fiction"],
        "tmdb_keywords": ["slow cinema", "space", "atmospheric"],
        "igdb_genres": ["Puzzle", "Adventure"],
        "igdb_themes": ["Science fiction", "Atmospheric", "Mystery"],
    },
    "Velvet Rebel": {
        "tmdb_genres": ["Crime", "Drama"],
        "tmdb_keywords": ["character study", "stealth", "crime"],
        "igdb_genres": ["Role-playing (RPG)", "Adventure"],
        "igdb_themes": ["Stealth", "Action", "Dark"],
    },
    "Solar Sprinter": {
        "tmdb_genres": ["Action", "Adventure"],
        "tmdb_keywords": ["action comedy", "racing", "adventure"],
        "igdb_genres": ["Platform", "Racing"],
        "igdb_themes": ["Action", "Comedy", "Arcade"],
    },
    "Hollow Wanderer": {
        "tmdb_genres": ["Drama", "Horror"],
        "tmdb_keywords": ["folk horror", "survival", "quiet"],
        "igdb_genres": ["Adventure", "Indie"],
        "igdb_themes": ["Survival", "Horror", "Fantasy"],
    },
    "Chrome Romantic": {
        "tmdb_genres": ["Comedy", "Music"],
        "tmdb_keywords": ["musical", "romantic comedy", "retro futurism"],
        "igdb_genres": ["Music", "Party"],
        "igdb_themes": ["Music", "Comedy", "Co-operative"],
    },
    "Echo Archivist": {
        "tmdb_genres": ["Documentary", "History"],
        "tmdb_keywords": ["classic film", "cult film", "historical"],
        "tmdb_date_range": ("1900-01-01", "2005-12-31"),
        "igdb_genres": ["Strategy", "Indie"],
        "igdb_themes": ["Turn-based strategy", "4X", "Retro"],
        "mapping_note": "Retro/niche indie/deep strategy; 4X and turn-based, no war themes.",
    },
}


def _items(result: CatalogResult, key: str | None = None) -> list[dict[str, Any]]:
    payload = result.data
    if key and isinstance(payload, dict):
        payload = payload.get(key, [])
    if isinstance(payload, dict):
        return [payload]
    return [item for item in payload if isinstance(item, dict)] if isinstance(payload, list) else []


def _name_ids(items: list[dict[str, Any]]) -> dict[str, int]:
    return {
        str(item["name"]).strip().casefold(): int(item["id"])
        for item in items
        if item.get("name") is not None and item.get("id") is not None
    }


async def _prepare(clients: CatalogClients) -> dict[str, Any]:
    """Resolve every catalog name before retrieval timing begins."""
    cached = getattr(clients, "_family_prepared_ids", None)
    if isinstance(cached, dict):
        return cached
    keyword_names = sorted(
        {name for intent in INTENTS.values() for name in intent["tmdb_keywords"]}
        | {name for profile in FILM_PROFILES.values() for name in profile.tmdb_keywords}
    )
    tmdb_genres, igdb_genres, igdb_themes, *keywords = await asyncio.gather(
        _safe_catalog_call(clients.tmdb_genres(), "genre/movie/list"),
        _safe_catalog_call(clients.igdb_genres(), "genres"),
        _safe_catalog_call(clients.igdb_themes(), "themes"),
        *(_resolve_keyword(clients, name) for name in keyword_names),
    )
    keyword_results = dict(zip(keyword_names, keywords, strict=True))
    unresolved = _keyword_errors(keyword_results)
    prepared = {
        "tmdb_genres": _name_ids(_items(tmdb_genres, "genres")) if tmdb_genres.ok else {},
        "tmdb_keywords": {
            name.casefold(): (
                int(_items(result, "results")[0]["id"])
                if result.ok
                and _items(result, "results")
                and _items(result, "results")[0].get("id") is not None
                else None
            )
            for name, result in keyword_results.items()
        },
        "keyword_results": {name.casefold(): result for name, result in keyword_results.items()},
        "unresolved_keywords": unresolved,
        "tmdb_genres_ok": tmdb_genres.ok,
        "igdb_available": (igdb_genres.ok or igdb_themes.ok),
        "igdb_genres": _name_ids(_items(igdb_genres)) if igdb_genres.ok else {},
        "igdb_themes": _name_ids(_items(igdb_themes)) if igdb_themes.ok else {},
        "boot_results": [tmdb_genres, igdb_genres, igdb_themes, *keywords],
    }
    for result in (tmdb_genres, igdb_genres, igdb_themes):
        if result.ok:
            continue
        provider = "TMDB" if result.endpoint.startswith("genre/") else "IGDB"
        identity = (provider, result.endpoint, str(result.error_code))
        if identity in _BOOT_FAILURE_LOGGED:
            continue
        _BOOT_FAILURE_LOGGED.add(identity)
        logger.warning(
            "upstream failure provider=%s endpoint=%s error=%s",
            provider,
            result.endpoint,
            redact_secrets(result.error_detail or result.error_message or "upstream failure")[:120],
        )
    clients._family_prepared_ids = prepared
    return prepared


def family_tmdb_query_groups(profile: Any, ids: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Build language, keyword, and genre queries with provenance labels."""
    genres = [
        ids["tmdb_genres"][name.casefold()]
        for name in profile.tmdb_genres
        if name.casefold() in ids["tmdb_genres"]
    ]
    keywords = [
        ids["tmdb_keywords"][name.casefold()]
        for name in profile.tmdb_keywords
        if ids["tmdb_keywords"].get(name.casefold()) is not None
    ]
    excludes = [
        ids["tmdb_genres"][name.casefold()]
        for name in profile.exclude_genres
        if name.casefold() in ids["tmdb_genres"]
    ]
    output = []
    query_groups: list[tuple[str, list[int], list[int], str | None, int]] = []
    for language in profile.original_languages:
        query_groups.append(("language_genre", genres, [], language, 100))
    if genres and keywords:
        query_groups.append(("keyword", genres, keywords, None, 500))
    if genres:
        query_groups.append(("genre", genres, [], None, 300))
    for source, query_genres, query_keywords, language, vote_floor in query_groups:
        for page in (1, 2):
            params = build_tmdb_params(query_genres, query_keywords, page)
            if excludes:
                params["without_genres"] = "|".join(map(str, excludes))
            if language:
                params["with_original_language"] = language
            params["vote_count.gte"] = vote_floor
            params["sort_by"] = "popularity.desc"
            output.append((source, params))
    return output


def family_igdb_bodies(profile: Any, ids: dict[str, Any]) -> list[str]:
    """Build family genre, theme, and combined IGDB query bodies."""
    genres = [
        ids["igdb_genres"][name.casefold()]
        for name in profile.igdb_genres
        if name.casefold() in ids["igdb_genres"]
    ]
    themes = [
        ids["igdb_themes"][name.casefold()]
        for name in profile.igdb_themes
        if name.casefold() in ids["igdb_themes"]
    ]
    return [
        build_apicalypse(genres, themes, mode)
        for mode in ("genres", "themes", "combined")
        if (genres if mode == "genres" else themes if mode == "themes" else genres and themes)
    ]


async def _safe_catalog_call(awaitable: Any, endpoint: str) -> CatalogResult:
    try:
        return await awaitable
    except ConfigError:
        raise
    except Exception as exc:
        detail = f"{type(exc).__name__}: {str(exc)[:120]}"
        return CatalogResult(endpoint, None, None, "transport_error", str(exc)[:120], detail)


async def _resolve_keyword(clients: CatalogClients, name: str) -> CatalogResult:
    result = await _safe_catalog_call(clients.tmdb_keyword(name), f"search/keyword:{name}")
    if result.status == "no_match":
        key = name.casefold()
        if key not in _NO_MATCH_LOGGED:
            logger.info("TMDB keyword has no exact match keyword=%s", name)
            _NO_MATCH_LOGGED.add(key)
    elif not result.ok:
        detail = redact_secrets(
            result.error_detail or result.error_message or f"HTTP {result.status}"
        )
        error_class = detail.split(":", 1)[0]
        message = detail.split(":", 1)[-1].strip()[:120]
        logger.warning(
            "upstream failure provider=TMDB endpoint=%s exception=%s message=%s",
            result.endpoint,
            error_class[:60],
            message,
        )
    return result


def normalize_catalog_name(value: str) -> str:
    """Normalize catalog titles for accent and punctuation-insensitive matching."""
    normalized = unicodedata.normalize("NFKD", value.casefold())
    ascii_text = "".join(char for char in normalized if not unicodedata.combining(char))
    words = re.findall(r"[a-z0-9]+", ascii_text)
    while words and words[0] in {"the", "a"}:
        words.pop(0)
    return " ".join(words)


def parse_anchor(anchor: str) -> tuple[str, int | None]:
    """Split a movie anchor into its title and year when supplied."""
    match = re.fullmatch(r"(.+?)\s*\((\d{4})\)", anchor.strip())
    return (match.group(1).strip(), int(match.group(2))) if match else (anchor.strip(), None)


async def resolve_tmdb_anchor(clients: CatalogClients, anchor: str) -> dict[str, Any] | None:
    """Resolve an exact title and a release year within one year of its anchor."""
    title, year = parse_anchor(anchor)
    result = await clients.tmdb_search_movie(title)
    return match_tmdb_anchor_result(result, title, year)


def match_tmdb_anchor_result(
    result: CatalogResult, title: str, year: int | None
) -> dict[str, Any] | None:
    """Select an exact title/year record from a TMDB search result."""
    if not result.ok:
        return None
    wanted = normalize_catalog_name(title)
    matches: list[tuple[int, int, dict[str, Any]]] = []
    for item in _items(result, "results"):
        actual_titles = (item.get("title", ""), item.get("original_title", ""))
        if wanted not in {normalize_catalog_name(str(value)) for value in actual_titles}:
            continue
        release = str(item.get("release_date", ""))
        try:
            actual_year = int(release[:4])
        except ValueError:
            continue
        if year is None or abs(actual_year - year) <= 1:
            year_distance = -abs(actual_year - year) if year else 0
            matches.append((int(item.get("vote_count", 0) or 0), year_distance, item))
    return max(matches, key=lambda row: (row[0], row[1]))[2] if matches else None


GAME_ANCHOR_ALIASES = {
    "gta san andreas": ("grand theft auto san andreas",),
    "grand theft auto san andreas": ("gta san andreas",),
    "def jam fight for ny": ("def jam fight for new york",),
    "def jam fight for new york": ("def jam fight for ny",),
    "tony hawks pro skater 2": ("tony hawks pro skater ii",),
    "tony hawks pro skater ii": ("tony hawks pro skater 2",),
}


def _game_names(item: dict[str, Any]) -> set[str]:
    """Return normalized IGDB primary and alternative names."""
    names = {normalize_catalog_name(str(item.get("name", "")))}
    alternatives = item.get("alternative_names", [])
    names.update(
        normalize_catalog_name(str(value.get("name", "")))
        for value in alternatives
        if isinstance(value, dict)
    )
    return names


async def resolve_igdb_anchor(
    clients: CatalogClients, anchor: str
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Resolve exact game names accent-insensitively and return similar records."""
    result = await clients.igdb_search_game(anchor)
    if not result.ok:
        return None, []
    wanted = normalize_catalog_name(anchor)
    aliases = {normalize_catalog_name(value) for value in GAME_ANCHOR_ALIASES.get(wanted, ())}
    expected = {wanted, *aliases}
    matches = [item for item in _items(result) if _game_names(item).intersection(expected)]
    matches.sort(key=lambda item: int(item.get("total_rating_count", 0) or 0), reverse=True)
    if not matches:
        return None, []
    full = await clients.igdb_games_by_ids(
        [int(matches[0]["id"])], rating_count_floor=None, rating_floor=None
    )
    candidate = next(iter(_items(full)), None) if full.ok else None
    return candidate, matches


def exact_igdb_anchor_matches(result: CatalogResult, anchor: str) -> list[dict[str, Any]]:
    """Return only accent-normalized exact IGDB game-name matches."""
    if not result.ok:
        return []
    wanted = normalize_catalog_name(anchor)
    aliases = {normalize_catalog_name(value) for value in GAME_ANCHOR_ALIASES.get(wanted, ())}
    expected = {wanted, *aliases}
    matches = [item for item in _items(result) if _game_names(item).intersection(expected)]
    return sorted(
        matches,
        key=lambda item: int(item.get("total_rating_count", 0) or 0),
        reverse=True,
    )


def _keyword_failure(result: CatalogResult) -> str:
    if result.status == "no_match":
        return "no_match"
    detail = result.error_detail or result.error_message or "upstream_error"
    return detail.split(":", 1)[0][:40]


def _keyword_errors(results: dict[str, CatalogResult]) -> dict[str, str]:
    """Return real keyword lookup failures, excluding ordinary no-match results."""
    return {
        name: _keyword_failure(result)
        for name, result in results.items()
        if not result.ok and result.status != "no_match"
    }


def _tmdb_ids(intent: dict[str, Any], ids: dict[str, Any]) -> tuple[list[int], list[int]]:
    genres = [
        ids["tmdb_genres"][name.casefold()]
        for name in intent["tmdb_genres"]
        if name.casefold() in ids["tmdb_genres"]
    ]
    keywords = [
        ids["tmdb_keywords"][name.casefold()]
        for name in intent["tmdb_keywords"]
        if ids["tmdb_keywords"].get(name.casefold()) is not None
    ]
    return genres, keywords


def _tmdb_queries(
    intent: dict[str, Any], ids: dict[str, Any], rung: str
) -> list[dict[str, str | int]]:
    genres, keywords = _tmdb_ids(intent, ids)
    if rung == "a":
        query_groups = [(genres, keywords, intent.get("tmdb_date_range"))]
    elif rung == "b":
        query_groups = [(genres, keywords, None)]
    elif rung == "c":
        query_groups = [(genres, [], None)]
    else:
        return []
    query_groups = [group for group in query_groups if any(group)]
    return [
        build_tmdb_params(group_genres, group_keywords, page, date_range)
        for group_genres, group_keywords, date_range in query_groups
        for page in (1, 2)
    ]


def _igdb_query_bodies(intent: dict[str, Any], ids: dict[str, Any]) -> list[tuple[str, str]]:
    genre_ids = [
        ids["igdb_genres"][name.casefold()]
        for name in intent["igdb_genres"]
        if name.casefold() in ids["igdb_genres"]
    ]
    theme_ids = [
        ids["igdb_themes"][name.casefold()]
        for name in intent["igdb_themes"]
        if name.casefold() in ids["igdb_themes"]
    ]
    genres = genre_ids
    themes = theme_ids
    return [
        ("genres", build_apicalypse(genres, themes, "genres")),
        ("themes", build_apicalypse(genres, themes, "themes")),
        ("combined", build_apicalypse(genres, themes, "combined")),
    ]


def _igdb_queries(intent: dict[str, Any], ids: dict[str, Any]) -> list[str]:
    return [body for _, body in _igdb_query_bodies(intent, ids)]


async def _fetch_intent(
    clients: CatalogClients,
    intent: dict[str, list[str]],
    ids: dict[str, Any],
    cold: bool,
) -> dict[str, Any]:
    """Fetch movie candidates through a three-rung fallback ladder."""
    started = time.perf_counter()
    all_calls: list[CatalogResult] = []
    selected_items: list[dict[str, Any]] = []
    rung_used = "c"
    for rung in ("a", "b", "c"):
        queries = _tmdb_queries(intent, ids, rung)
        calls = await asyncio.gather(
            *(clients.tmdb_discover(query, cold=cold) for query in queries)
        )
        all_calls.extend(calls)
        selected_items = _dedupe(calls, "results")
        rung_used = rung
        if len(selected_items) >= 60:
            break
    unresolved = [
        name
        for name in intent["tmdb_keywords"]
        if ids["tmdb_keywords"].get(name.casefold()) is None
    ]
    keyword_errors = ids.get("unresolved_keywords", {})
    degraded = (
        rung_used != "a"
        or len(selected_items) < 60
        or bool(unresolved)
        or any(not result.ok for result in all_calls)
    )
    return {
        "items": selected_items,
        "calls": all_calls,
        "rung_used": rung_used,
        "unresolved_keywords": unresolved,
        "keyword_errors": {name: keyword_errors.get(name, "no_match") for name in unresolved},
        "degraded": degraded,
        "movie_ms": (time.perf_counter() - started) * 1000,
    }


def _game_items(result: CatalogResult) -> list[dict[str, Any]]:
    """Extract games from regular and multiquery response shapes."""
    if result.endpoint != "multiquery":
        return _items(result)
    found: list[dict[str, Any]] = []
    if isinstance(result.data, list):
        for section in result.data:
            if isinstance(section, dict):
                records = section.get("result", [])
                if isinstance(records, list):
                    found.extend(item for item in records if isinstance(item, dict))
    return found


def _dedupe_games(results: list[CatalogResult]) -> list[dict[str, Any]]:
    """Deduplicate separate and multiquery game candidates by ID."""
    found: dict[str, dict[str, Any]] = {}
    for result in results:
        for item in _game_items(result):
            if item.get("id") is not None:
                found.setdefault(str(item["id"]), item)
    return list(found.values())


async def _fetch_igdb_approach(
    clients: CatalogClients,
    bodies: list[tuple[str, str]],
    mode: str,
    cold: bool,
) -> dict[str, Any]:
    """Run either three IGDB requests or one multiquery request."""
    started = time.perf_counter()
    if mode == "separate":
        calls = await asyncio.gather(*(clients.igdb_games(body, cold=cold) for _, body in bodies))
    else:
        calls = [await clients.igdb_multiquery(build_multiquery(bodies), cold=cold)]
    total_ms = (time.perf_counter() - started) * 1000
    return {
        "calls": calls,
        "items": _dedupe_games(calls),
        "total_ms": total_ms,
        "api_ms": max((call.api_ms for call in calls), default=0.0),
    }


def _dedupe(results: list[CatalogResult], key: str) -> list[dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for result in results:
        for item in _items(result, key):
            item_id = item.get("id")
            if item_id is not None:
                found.setdefault(str(item_id), item)
    return list(found.values())


def _latency_stats(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"p50": None, "max": None}
    return {"p50": round(statistics.median(values), 2), "max": round(max(values), 2)}


def _call_metrics(results: list[CatalogResult], item_key: str) -> list[dict[str, Any]]:
    return [
        {
            "endpoint": result.endpoint,
            "status": result.status,
            "error_code": result.error_code,
            "error_detail": result.error_detail,
            "attempt": result.attempt,
            "latency_ms": round(result.latency_ms, 2),
            "limiter_wait_ms": round(result.limiter_wait_ms, 2),
            "api_ms": round(result.api_ms, 2),
            "status_error": (
                result.error_message[:120]
                if result.error_code is not None and result.error_detail is None
                else None
            ),
            "item_count": len(_items(result, item_key)),
        }
        for result in results
    ]


def _game_call_metrics(results: list[CatalogResult]) -> list[dict[str, Any]]:
    """Build per-call metrics for separate or multiquery IGDB responses."""
    return [
        {
            "endpoint": result.endpoint,
            "status": result.status,
            "error_code": result.error_code,
            "error_detail": result.error_detail,
            "attempt": result.attempt,
            "latency_ms": round(result.latency_ms, 2),
            "limiter_wait_ms": round(result.limiter_wait_ms, 2),
            "api_ms": round(result.api_ms, 2),
            "status_error": (
                result.error_message[:120]
                if result.error_code is not None and result.error_detail is None
                else None
            ),
            "item_count": len(_game_items(result)),
        }
        for result in results
    ]


def _jaccard_report(profiles: list[dict[str, Any]], id_key: str) -> dict[str, Any]:
    """Calculate all pairwise candidate-ID Jaccard similarities."""
    pairs = []
    for left_index, left in enumerate(profiles):
        left_ids = set(left[id_key])
        for right in profiles[left_index + 1 :]:
            right_ids = set(right[id_key])
            union = left_ids | right_ids
            score = len(left_ids & right_ids) / len(union) if union else 0.0
            pairs.append(
                {
                    "left": left["archetype"],
                    "right": right["archetype"],
                    "jaccard": round(score, 4),
                    "overlap_flag": score > 0.5,
                }
            )
    scores = [pair["jaccard"] for pair in pairs]
    return {
        "mean": round(statistics.mean(scores), 4) if scores else 0.0,
        "max": max(scores, default=0.0),
        "flagged_pairs": [pair for pair in pairs if pair["overlap_flag"]],
        "pairs": pairs,
    }


def _table_lines(report: dict[str, Any]) -> list[str]:
    """Build stdout lines from the completed report rows."""
    lines = [
        "archetype | movies | games | rung | degraded | unresolved kw | "
        "movie_ms p50/max | game_ms total/api p50/max | total_ms p50/max | "
        "top movies | top games"
    ]
    for result in report["archetypes"]:
        latency = result["latency_ms"]
        lines.append(
            f"{result['archetype']} | {result['movie_count']} | {result['game_count']} | "
            f"{result['rung_used']} | {result['degraded']} | "
            f"{', '.join(result['unresolved_keywords']) or '-'} | "
            f"{_format_stats(latency['movie_ms'])} | "
            f"{_format_stats(latency['game_total_ms'])}/"
            f"{_format_stats(latency['game_api_ms'])} | "
            f"{_format_stats(latency['total_ms'])} | "
            f"{', '.join(result['top_movie_titles'])} | "
            f"{', '.join(result['top_game_titles'])}"
        )
    return lines


def _approach_summary(samples: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize total and API-only IGDB latency for an approach."""
    return {
        "total_ms": _latency_stats([sample["total_ms"] for sample in samples]),
        "api_ms": _latency_stats([sample["api_ms"] for sample in samples]),
        "http_requests_per_run": 3 if samples and samples[0]["mode"] == "separate" else 1,
        "subqueries_per_run": 3,
    }


async def run_probe(cold: bool = False, igdb_mode: str = "auto") -> dict[str, Any]:
    """Run the catalog latency spike and save its report."""
    clients = CatalogClients()
    try:
        connection_times = {
            "tmdb": await clients.measure_connect_tls("tmdb"),
            "igdb": await clients.measure_connect_tls("igdb"),
        }
        ids = await _prepare(clients)
        profile_runs: list[dict[str, Any]] = []
        for archetype, intent in INTENTS.items():
            runs: list[dict[str, Any]] = []
            bodies = _igdb_query_bodies(intent, ids)
            for repeat in range(3):
                force_cold = cold or repeat == 0
                started = time.perf_counter()
                movie_run, separate_run, multiquery_run = await asyncio.gather(
                    _fetch_intent(clients, intent, ids, force_cold),
                    _fetch_igdb_approach(clients, bodies, "separate", force_cold),
                    _fetch_igdb_approach(clients, bodies, "multiquery", force_cold),
                )
                runs.append(
                    {
                        "movie": movie_run,
                        "separate": separate_run,
                        "multiquery": multiquery_run,
                        "total_ms": (time.perf_counter() - started) * 1000,
                        "kind": "cold" if force_cold else "warm",
                    }
                )
            profile_runs.append(
                {
                    "archetype": archetype,
                    "intent": intent,
                    "runs": runs,
                }
            )
        approach_samples = {
            mode: [
                {"total_ms": run[mode]["total_ms"], "api_ms": run[mode]["api_ms"], "mode": mode}
                for profile in profile_runs
                for run in profile["runs"]
            ]
            for mode in ("separate", "multiquery")
        }
        measured_default = min(
            approach_samples,
            key=lambda mode: statistics.median(
                sample["total_ms"] for sample in approach_samples[mode]
            ),
        )
        selected_mode = measured_default if igdb_mode == "auto" else igdb_mode
        report_rows = []
        for profile in profile_runs:
            runs = profile["runs"]
            intent = profile["intent"]
            movie_calls = [call for run in runs for call in run["movie"]["calls"]]
            movie_items = _dedupe(movie_calls, "results")
            selected_calls = [call for run in runs for call in run[selected_mode]["calls"]]
            game_items = _dedupe_games(selected_calls)
            unresolved = [
                name
                for name in intent["tmdb_keywords"]
                if ids["tmdb_keywords"].get(name.casefold()) is None
            ]
            movie_stats = _latency_stats([run["movie"]["movie_ms"] for run in runs])
            game_total_stats = _latency_stats([run[selected_mode]["total_ms"] for run in runs])
            game_api_stats = _latency_stats([run[selected_mode]["api_ms"] for run in runs])
            total_stats = _latency_stats(
                [max(run["movie"]["movie_ms"], run[selected_mode]["total_ms"]) for run in runs]
            )
            rung_used = runs[0]["movie"]["rung_used"]
            degraded = (
                any(run["movie"]["degraded"] for run in runs)
                or bool(unresolved)
                or len(movie_items) < 60
                or any(not call.ok for call in selected_calls)
            )
            keyword_metrics = {}
            for name in intent["tmdb_keywords"]:
                result = ids["keyword_results"][name.casefold()]
                keyword_metrics[name] = _call_metrics([result], "results")[0]
            report_rows.append(
                {
                    "archetype": profile["archetype"],
                    "movie_count": len(movie_items),
                    "game_count": len(game_items),
                    "rung_used": rung_used,
                    "degraded": degraded,
                    "unresolved_keywords": unresolved,
                    "keyword_lookups": keyword_metrics,
                    "latency_ms": {
                        "movie_ms": movie_stats,
                        "game_total_ms": game_total_stats,
                        "game_api_ms": game_api_stats,
                        "total_ms": total_stats,
                    },
                    "igdb_mode": selected_mode,
                    "igdb_mapping_note": intent.get("mapping_note"),
                    "igdb_approaches": {
                        mode: {
                            **_approach_summary(
                                [
                                    {
                                        "mode": mode,
                                        "total_ms": run[mode]["total_ms"],
                                        "api_ms": run[mode]["api_ms"],
                                    }
                                    for run in runs
                                ]
                            ),
                            "calls": _game_call_metrics(
                                [call for run in runs for call in run[mode]["calls"]]
                            ),
                        }
                        for mode in ("separate", "multiquery")
                    },
                    "calls": {
                        "tmdb": _call_metrics(movie_calls, "results"),
                        "igdb": _game_call_metrics(selected_calls),
                    },
                    "top_movie_titles": [item.get("title", "") for item in movie_items[:5]],
                    "top_game_titles": [item.get("name", "") for item in game_items[:5]],
                    "movie_ids": [item["id"] for item in movie_items],
                    "game_ids": [item["id"] for item in game_items],
                }
            )
        movie_jaccard = _jaccard_report(report_rows, "movie_ids")
        game_jaccard = _jaccard_report(report_rows, "game_ids")
        report = {
            "cold_requested": cold,
            "igdb_mode_requested": igdb_mode,
            "igdb_default_mode": measured_default,
            "igdb_request_count_basis": (
                "HTTP requests sent by this probe; multiquery contains 3 subqueries in 1 request."
            ),
            "connect_tls": connection_times,
            "differentiation": {"movies": movie_jaccard, "games": game_jaccard},
            "id_list_boot_calls": [
                _call_metrics([result], "")[0] for result in ids["boot_results"]
            ],
            "archetypes": report_rows,
        }
        for row in report_rows:
            row.pop("movie_ids")
            row.pop("game_ids")
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
        for line in _table_lines(report):
            print(line)
        print(f"Jaccard movies mean/max | {movie_jaccard['mean']}/{movie_jaccard['max']}")
        print(f"Jaccard games mean/max | {game_jaccard['mean']}/{game_jaccard['max']}")
        print(f"IGDB default mode | {measured_default}")
        print(f"connect+TLS | TMDB {connection_times['tmdb']} | IGDB {connection_times['igdb']}")
        return report
    finally:
        await clients.close()


def _format_stats(stats: dict[str, float | None]) -> str:
    return f"{stats['p50']}/{stats['max']}"


def main() -> int:
    """Run the async catalog retrieval probe."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--cold", action="store_true")
    parser.add_argument("--igdb-mode", choices=("auto", "separate", "multiquery"), default="auto")
    args = parser.parse_args()
    asyncio.run(run_probe(cold=args.cold, igdb_mode=args.igdb_mode))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
