"""Explain how a fixture title reached (or missed) a recommendation list."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

from app.services.catalog.clients import CatalogClients
from app.services.catalog.family_profiles import FILM_PROFILES, GAME_PROFILES
from app.services.catalog.retrieval import (
    _prepare,
    match_tmdb_anchor_result,
    normalize_catalog_name,
    parse_anchor,
)
from app.services.ranking import (
    RankingService,
    _catalog_maps,
    _dict_list,
    _merge_family_items,
    _score_candidate,
)
from app.services.vibe.resolver import VibeResolver
from spike.run_resolver import DEFAULT_PROFILES, LastFmDiskCache, _artist_input


def _find_profile(profile_id: str, path: Path) -> dict[str, Any]:
    profiles = json.loads(path.read_text(encoding="utf-8"))
    return next(profile for profile in profiles if profile["id"] == profile_id)


async def explain(profile_id: str, title: str, kind: str, profiles_path: Path) -> None:
    """Run catalog ranking and print source, filters, score parts, and slot outcome."""
    profile = _find_profile(profile_id, profiles_path)
    artists = [_artist_input(name, LastFmDiskCache()) for name in profile["artists"]]
    vibe = VibeResolver().resolve_profile(artists)
    families = [row for row in vibe["top_families"] if row["share"] >= 0.08][:4]
    family_ids = [row["id"] for row in families]
    seed = hashlib.sha256("|".join(sorted(profile["artists"])).encode()).digest()
    clients = CatalogClients()
    ranker = RankingService(clients)
    try:
        result = await ranker.rank(
            vibe["vector"],
            vibe["primary"],
            vibe["secondary"],
            top_families=vibe["top_families"],
            top_artist_names=profile["artists"],
        )
        ids = await _prepare(clients)
        pools = await asyncio.gather(
            *(ranker._family_pool(family_id, ids, seed) for family_id in family_ids)
        )
        catalog_kind = "movies" if kind == "movie" else "games"
        rows = _merge_family_items(
            pools,
            family_ids,
            catalog_kind,
            anchor_rec_families={
                family_ids[index]
                for index, family in enumerate(families[:2])
                if family["share"] >= 0.15
            },
        )
        maps = _catalog_maps(_dict_list(ids.get("tmdb_genres", {})))
        genre_map = _catalog_maps(_dict_list(ids.get("igdb_genres", {})))
        theme_map = _catalog_maps(_dict_list(ids.get("igdb_themes", {})))
        target = normalize_catalog_name(title)
        final_list = result[catalog_kind]
        final_by_id = {str(item["id"]): index + 1 for index, item in enumerate(final_list)}
        found = 0
        for family_id, _pool in zip(family_ids, pools, strict=True):
            profile_spec = FILM_PROFILES[family_id] if kind == "movie" else GAME_PROFILES[family_id]
            anchors = profile_spec.anchors
            for anchor in anchors:
                anchor_title, year = parse_anchor(anchor) if kind == "movie" else (anchor, None)
                if normalize_catalog_name(anchor_title) != target:
                    continue
                found += 1
                if kind == "movie":
                    search = await clients.tmdb_search_movie(anchor_title)
                    item = match_tmdb_anchor_result(search, anchor_title, year)
                    item_id = str(item.get("id")) if item else "unresolved"
                    print(f"anchor family={family_id} anchor={anchor} resolved_id={item_id}")
                    if item:
                        genre_ids = {
                            ids.get("tmdb_genres", {}).get(name.casefold())
                            for name in profile_spec.tmdb_genres
                        }
                        exclude_ids = {
                            ids.get("tmdb_genres", {}).get(name.casefold())
                            for name in profile_spec.exclude_genres
                        }
                        print(
                            "filters | language dropped=anchor bypass | genres overlap="
                            f"{bool(set(item.get('genre_ids', [])) & genre_ids)} | "
                            "vote floor dropped=anchor bypass | exclusion overlap="
                            f"{bool(set(item.get('genre_ids', [])) & exclude_ids)}"
                        )
                else:
                    matching = [
                        row
                        for row in rows
                        if normalize_catalog_name(str(row["item"].get("name", ""))) == target
                    ]
                    item_id = str(matching[0]["item"].get("id")) if matching else "unresolved"
                    print(f"anchor family={family_id} anchor={anchor} resolved_id={item_id}")
                scored = next((row for row in rows if str(row["item"].get("id")) == item_id), None)
                if scored:
                    detail = _score_candidate(
                        scored,
                        "movie" if kind == "movie" else "game",
                        vibe["vector"],
                        vibe["primary"],
                        families,
                        maps,
                        genre_map,
                        theme_map,
                        "https://image.tmdb.org/t/p/",
                    )
                    print(
                        f"retrieval_source={detail['reason_source']} components="
                        + ", ".join(
                            f"{name}={value:.4f}" for name, value in detail["_components"].items()
                        )
                    )
                decision = (
                    f"selected rank={final_by_id[item_id]}"
                    if item_id in final_by_id
                    else "not selected (outside candidate pool or displaced by slot/MMR rules)"
                )
                print(f"slot decision={decision}")
        if not found:
            print(
                f"No matching {kind} anchor for {title!r} in profile {profile_id} "
                f"families: {family_ids}"
            )
            for item in final_list:
                if normalize_catalog_name(item["title"]) == target:
                    print(
                        f"found in output id={item['id']} source={item['reason_source']} "
                        f"family={item['matched_family']['id']} score={item['score']:.4f}"
                    )
    finally:
        await clients.close()


def main() -> int:
    """Parse arguments and run the diagnostic."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--kind", choices=("movie", "game"), default="movie")
    parser.add_argument("--profiles", type=Path, default=DEFAULT_PROFILES)
    args = parser.parse_args()
    asyncio.run(explain(args.profile, args.title, args.kind, args.profiles))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
