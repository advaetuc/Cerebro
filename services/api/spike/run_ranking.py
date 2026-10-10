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
    p02_rows = p02[:10]
    p06 = by_id.get("p06", {})
    p06_shares = {row["id"]: float(row["share"]) for row in p06.get("top_families", [])}
    p06_picks = p06.get("movies", []) + p06.get("games", [])
    all_picks = [item for row in results for item in row["movies"] + row["games"]]
    anchor_rec_count = sum(item.get("reason_source") == "anchor_rec" for item in all_picks)
    anchor_and_language_count = sum(
        item.get("reason_source") in {"anchor", "language_genre"} for item in all_picks
    )
    denominator = len(all_picks) or 1
    checks = {
        "p01 >=4/5 Hindi top-five films": sum(
            item.get("original_language") == "hi" for item in p01[:5]
        )
        >= 4,
        "p02 >=1 Punjabi top-ten pick": any(
            item.get("original_language") == "pa"
            or item.get("matched_family", {}).get("id") == "punjabi-pop"
            for item in p02_rows
        ),
        "p03 Gully Boy top ten": any("gully boy" in title for title in p03_titles),
        "p06 no picks from families below 0.10": all(
            p06_shares.get(item.get("matched_family", {}).get("id", ""), 0) >= 0.10
            for item in p06_picks
        ),
        "anchor_rec share <=25%": anchor_rec_count / denominator <= 0.25,
        "anchor + language_genre share >=50%": anchor_and_language_count / denominator >= 0.5,
        "no title in top fives of >=3 profiles": all(len(ids) < 3 for ids in repeated.values()),
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
