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
                print(f"  {kind}: title | matched family | reason source | why")
                for item in row[kind][:5]:
                    family = item.get("matched_family", {}).get("label", "—")
                    print(
                        f"  {item['title']} | {family} | {item.get('reason_source', '—')} "
                        f"| {item['why']}"
                    )
    finally:
        await clients.close()
    report = {"profiles": results}
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def main() -> int:
    """Run profile ranking evaluation."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--profiles", type=Path, default=DEFAULT_PROFILES)
    args = parser.parse_args()
    asyncio.run(run(args.profiles))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
