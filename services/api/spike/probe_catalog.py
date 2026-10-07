"""Measure cached and cold TMDB and IGDB candidate retrieval."""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from pathlib import Path
from typing import Any

from spike.catalog_clients import (
    CatalogClients,
    CatalogResult,
    build_apicalypse,
    build_tmdb_params,
)

SPIKE_DIR = Path(__file__).resolve().parent
BLUEPRINT_PATH = SPIKE_DIR.parents[2] / "docs" / "cerebro_blueprint.md"
REPORT_PATH = SPIKE_DIR / "out" / "catalog_report.json"

INTENTS: dict[str, dict[str, list[str]]] = {
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
        "igdb_genres": ["Strategy", "Adventure"],
        "igdb_themes": ["Historical", "Fantasy", "Retro"],
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
    keyword_names = sorted(
        {name for intent in INTENTS.values() for name in intent["tmdb_keywords"]}
    )
    tmdb_genres, igdb_genres, igdb_themes, *keywords = await asyncio.gather(
        clients.tmdb_genres(),
        clients.igdb_genres(),
        clients.igdb_themes(),
        *(clients.tmdb_keyword(name) for name in keyword_names),
    )
    return {
        "tmdb_genres": _name_ids(_items(tmdb_genres, "genres")),
        "tmdb_keywords": {
            name.casefold(): next(iter(_name_ids(_items(result, "results")).values()), None)
            for name, result in zip(keyword_names, keywords, strict=True)
        },
        "igdb_genres": _name_ids(_items(igdb_genres)),
        "igdb_themes": _name_ids(_items(igdb_themes)),
        "boot_results": [tmdb_genres, igdb_genres, igdb_themes, *keywords],
    }


def _tmdb_queries(intent: dict[str, list[str]], ids: dict[str, Any]) -> list[dict[str, str | int]]:
    genre_ids = [
        ids["tmdb_genres"][name.casefold()]
        for name in intent["tmdb_genres"]
        if name.casefold() in ids["tmdb_genres"]
    ]
    keyword_ids = [
        ids["tmdb_keywords"][name.casefold()]
        for name in intent["tmdb_keywords"]
        if ids["tmdb_keywords"].get(name.casefold()) is not None
    ]
    queries = []
    for genre_id in genre_ids:
        for keyword_id in keyword_ids:
            queries.append(build_tmdb_params([genre_id], [keyword_id]))
    if not keyword_ids:
        queries = [build_tmdb_params([genre_id], []) for genre_id in genre_ids]
    if not genre_ids and keyword_ids:
        queries = [build_tmdb_params([], [keyword_id]) for keyword_id in keyword_ids]
    return queries[:6]


def _igdb_queries(intent: dict[str, list[str]], ids: dict[str, Any]) -> list[str]:
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
    pairs = []
    for index in range(3):
        genre = [genre_ids[index % len(genre_ids)]] if genre_ids else []
        theme = [theme_ids[index % len(theme_ids)]] if theme_ids else []
        pairs.append((genre, theme))
    return [build_apicalypse(genres, themes) for genres, themes in pairs[:3]]


async def _fetch_intent(
    clients: CatalogClients,
    intent: dict[str, list[str]],
    ids: dict[str, Any],
    cold: bool,
) -> tuple[list[CatalogResult], list[CatalogResult], float, float, float]:
    """Fetch one archetype's movie and game candidates concurrently."""
    movie_queries = _tmdb_queries(intent, ids)
    game_queries = _igdb_queries(intent, ids)
    movie_started = time.perf_counter()

    async def fetch_movies() -> tuple[list[CatalogResult], float]:
        results = await asyncio.gather(
            *(clients.tmdb_discover(query, cold=cold) for query in movie_queries)
        )
        return results, (time.perf_counter() - movie_started) * 1000

    game_started = time.perf_counter()

    async def fetch_games() -> tuple[list[CatalogResult], float]:
        results = await asyncio.gather(
            *(clients.igdb_games(query, cold=cold) for query in game_queries)
        )
        return results, (time.perf_counter() - game_started) * 1000

    total_started = time.perf_counter()
    (movie_results, movie_ms), (game_results, game_ms) = await asyncio.gather(
        fetch_movies(), fetch_games()
    )
    total_ms = (time.perf_counter() - total_started) * 1000
    return movie_results, game_results, movie_ms, game_ms, total_ms


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
            "latency_ms": round(result.latency_ms, 2),
            "item_count": len(_items(result, item_key)),
        }
        for result in results
    ]


def _table_lines(report: dict[str, Any]) -> list[str]:
    """Build stdout lines from the completed report rows."""
    lines = [
        "archetype | movies | games | movie_ms cold/warm p50/max | "
        "game_ms cold/warm p50/max | total_ms cold/warm p50/max | "
        "top movies | top games"
    ]
    for result in report["archetypes"]:
        latency = result["latency_ms"]
        lines.append(
            f"{result['archetype']} | {result['movie_count']} | {result['game_count']} | "
            f"{_format_pair(latency['cold']['movie_ms'], latency['warm']['movie_ms'])} | "
            f"{_format_pair(latency['cold']['game_ms'], latency['warm']['game_ms'])} | "
            f"{_format_pair(latency['cold']['total_ms'], latency['warm']['total_ms'])} | "
            f"{', '.join(result['top_movie_titles'])} | "
            f"{', '.join(result['top_game_titles'])}"
        )
    return lines


async def run_probe(cold: bool = False) -> dict[str, Any]:
    """Run the catalog latency spike and save its report."""
    clients = CatalogClients()
    try:
        connection_times = {
            "tmdb": await clients.measure_connect_tls("tmdb"),
            "igdb": await clients.measure_connect_tls("igdb"),
        }
        ids = await _prepare(clients)
        report_rows = []
        for archetype, intent in INTENTS.items():
            repeats: list[dict[str, Any]] = []
            all_movie_results: list[CatalogResult] = []
            all_game_results: list[CatalogResult] = []
            for repeat in range(3):
                force_cold = cold or repeat == 0
                movies, games, movie_ms, game_ms, total_ms = await _fetch_intent(
                    clients, intent, ids, force_cold
                )
                all_movie_results.extend(movies)
                all_game_results.extend(games)
                repeats.append({
                    "movie_ms": movie_ms,
                    "game_ms": game_ms,
                    "total_ms": total_ms,
                    "kind": "cold" if force_cold else "warm",
                })
            movie_items = _dedupe(all_movie_results, "results")
            game_items = _dedupe(all_game_results, "")
            cold_samples = [sample for sample in repeats if sample["kind"] == "cold"]
            warm_samples = [sample for sample in repeats if sample["kind"] == "warm"]
            stats = {
                mode: {
                    metric: _latency_stats([float(sample[metric]) for sample in samples])
                    for metric in ("movie_ms", "game_ms", "total_ms")
                }
                for mode, samples in (("cold", cold_samples), ("warm", warm_samples))
            }
            report_rows.append({
                "archetype": archetype,
                "movie_count": len(movie_items),
                "game_count": len(game_items),
                "latency_ms": stats,
                "repeats": repeats,
                "calls": {
                    "tmdb": _call_metrics(all_movie_results, "results"),
                    "igdb": _call_metrics(all_game_results, ""),
                },
                "top_movie_titles": [item.get("title", "") for item in movie_items[:5]],
                "top_game_titles": [item.get("name", "") for item in game_items[:5]],
            })
        report = {
            "cold_requested": cold,
            "connect_tls": connection_times,
            "id_list_boot_calls": [
                {
                    "endpoint": result.endpoint,
                    "status": result.status,
                    "error_code": result.error_code,
                }
                for result in ids["boot_results"]
            ],
            "archetypes": report_rows,
        }
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
        for line in _table_lines(report):
            print(line)
        print(f"connect+TLS | TMDB {connection_times['tmdb']} | IGDB {connection_times['igdb']}")
        return report
    finally:
        await clients.close()


def _format_stats(stats: dict[str, float | None]) -> str:
    return f"{stats['p50']}/{stats['max']}"


def _format_pair(
    cold_stats: dict[str, float | None], warm_stats: dict[str, float | None]
) -> str:
    """Format cold and warm median/max values compactly."""
    warm_text = _format_stats(warm_stats) if warm_stats["p50"] is not None else "-"
    return f"{_format_stats(cold_stats)}/{warm_text}"


def main() -> int:
    """Run the async catalog retrieval probe."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--cold", action="store_true")
    args = parser.parse_args()
    asyncio.run(run_probe(cold=args.cold))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
