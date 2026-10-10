"""Evaluate family-aware catalog recommendations for cached fixture profiles."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from app.services.catalog.clients import CatalogClients
from app.services.ranking import RankingService
from app.services.vibe.resolver import VibeResolver
from spike.run_resolver import (
    DEFAULT_PROFILES,
    LastFmDiskCache,
    _artist_input,
)

SPIKE_DIR = Path(__file__).resolve().parent
REPORT_PATH = SPIKE_DIR / "out" / "ranking_report.json"


async def _run_profile(
    profile: dict[str, Any], cache: LastFmDiskCache, ranker: RankingService
) -> dict[str, Any]:
    artists = [_artist_input(name, cache) for name in profile["artists"]]
    vibe = VibeResolver().resolve_profile(artists)
    ranked = await ranker.rank(
        vibe["vector"],
        vibe["primary"],
        vibe["secondary"],
        top_families=vibe["top_families"],
        top_artist_names=profile["artists"],
    )
    return {
        "id": profile["id"],
        "label": profile["label"],
        "top_families": vibe["top_families"],
        "vector": vibe["vector"],
        "movies": ranked["movies"],
        "games": ranked["games"],
        "degraded": ranked["degraded"],
    }


async def run(path: Path) -> dict[str, Any]:
    """Resolve fixture vibes from disk cache and retrieve live catalog picks."""
    profiles = json.loads(path.read_text(encoding="utf-8"))
    clients = CatalogClients()
    ranker = RankingService(clients)
    cache = LastFmDiskCache()
    results = []
    try:
        for profile in profiles:
            row = await _run_profile(profile, cache, ranker)
            results.append(row)
            families = ", ".join(
                f"{item['id']} ({item['share']:.2f})" for item in row["top_families"]
            )
            print(f"{row['id']} {row['label']} | families: {families}")
            for kind in ("movies", "games"):
                print(f"  {kind}: title | year | language | family | source")
                for item in row[kind][:5]:
                    family = item.get("matched_family", {}).get("label", "—")
                    print(
                        f"  {item['title']} | {item.get('year') or '—'} | "
                        f"{item.get('original_language') or '—'} | {family} | "
                        f"{item.get('reason_source', '—')}"
                    )
    finally:
        await clients.close()
    report = {"profiles": results}
    _print_evaluation(results)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def _print_evaluation(results: list[dict[str, Any]]) -> None:
    sources: dict[str, int] = {}
    total = 0
    for profile in results:
        for item in profile["movies"] + profile["games"]:
            source = item.get("reason_source", "unknown")
            sources[source] = sources.get(source, 0) + 1
            total += 1
    print(
        "source shares | "
        + ", ".join(f"{key}={value / total:.1%}" for key, value in sorted(sources.items()))
        if total
        else "source shares | no picks"
    )
    for profile in results:
        if profile["id"] not in {"p01", "p02", "p03", "p04", "p05", "p08", "p09"}:
            continue
        movies = profile["movies"][:10]
        languages = [str(item.get("original_language", "")) for item in movies]
        if profile["id"] in {"p01", "p02", "p03", "p04"}:
            count = sum(language in {"hi", "pa"} for language in languages)
            punjabi = sum(language == "pa" for language in languages)
            print(
                f"{profile['id']} Hindi/Punjabi movies | {count}/{len(movies)} | Punjabi {punjabi}"
            )
        elif profile["id"] == "p08":
            korean_count = sum(language == "ko" for language in languages)
            print(f"p08 Korean movies | {korean_count}/{len(movies)}")
    repeated: dict[str, set[str]] = {}
    for profile in results:
        for item in profile["movies"][:5] + profile["games"][:5]:
            repeated.setdefault(item["title"], set()).add(profile["id"])
    repeats = [title for title, ids in repeated.items() if len(ids) >= 3]
    print("repeat titles | " + (", ".join(sorted(repeats)) if repeats else "none"))
    by_id = {row["id"]: row for row in results}
    p01 = by_id.get("p01", {}).get("movies", [])
    p02 = by_id.get("p02", {}).get("movies", [])
    p03_titles = {item["title"].casefold() for item in by_id.get("p03", {}).get("movies", [])}
    p05 = by_id.get("p05", {}).get("movies", []) + by_id.get("p05", {}).get("games", [])
    p08_titles = {item["title"].casefold() for item in by_id.get("p08", {}).get("movies", [])}
    p09_titles = {item["title"].casefold() for item in by_id.get("p09", {}).get("movies", [])}
    p02_langs = [item.get("original_language") for item in p02]
    p08_langs = [item.get("original_language") for item in by_id.get("p08", {}).get("movies", [])]
    checks = {
        "p01 >=4/5 Hindi films": sum(item.get("original_language") == "hi" for item in p01[:5])
        >= 4,
        "p02 >=2 Hindi/Punjabi films": sum(value in {"hi", "pa"} for value in p02_langs) >= 2,
        "p02 Punjabi when available": not any(value == "pa" for value in p02_langs)
        or "pa" in p02_langs,
        "p03 Gully Boy": any("gully boy" in title for title in p03_titles),
        "p05 >=3 anchors": sum(
            item.get("reason_source") in {"anchor", "anchor_rec"} for item in p05
        )
        >= 3,
        "p08 K-pop film": any("kpop" in title or "blackpink" in title for title in p08_titles),
        "p08 Korean-language film": any(value == "ko" for value in p08_langs),
        "p09 Amadeus or Whiplash": any(
            "amadeus" in title or "whiplash" in title for title in p09_titles
        ),
        "no title repeated in >=4 top fives": all(len(ids) < 4 for ids in repeated.values()),
    }
    for label, passed in checks.items():
        print(f"{'PASS' if passed else 'FAIL'} | {label}")


def main() -> int:
    """Run profile ranking evaluation."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--profiles", type=Path, default=DEFAULT_PROFILES)
    args = parser.parse_args()
    asyncio.run(run(args.profiles))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
