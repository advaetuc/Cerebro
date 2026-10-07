"""Run the Vibe Resolver on cached Last.fm fixture artist data."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from app.services.vibe.genre_priors import DIMENSIONS, FAMILIES
from app.services.vibe.resolver import ArtistInput, VibeResolver

SPIKE_DIR = Path(__file__).resolve().parent
DEFAULT_PROFILES = SPIKE_DIR / "fixtures" / "profiles.json"
REPORT_PATH = SPIKE_DIR / "out" / "resolver_report.json"
EXPECTED_PATH = SPIKE_DIR / "fixtures" / "expected_archetypes.json"
CACHE_DIR = SPIKE_DIR / ".cache"
JUNK_TAGS = frozenset(
    {"seen live", "favorites", "favourite", "favourites", "albums i own", "spotify"}
)


class LastFmDiskCache:
    """Read Last.fm response bodies from the existing disk cache only."""

    def __init__(self, cache_dir: Path = CACHE_DIR) -> None:
        self.cache_dir = cache_dir

    def response(self, method: str, **params: str | int) -> dict[str, Any]:
        """Return one cached response body, or an empty mapping on cache miss."""
        cache_key = json.dumps(
            {"method": method, "params": sorted(params.items())},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        digest = hashlib.sha256(cache_key.encode("utf-8")).hexdigest()
        try:
            cached = json.loads((self.cache_dir / f"{digest}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        data = cached.get("data", {})
        return data if isinstance(data, dict) else {}


def _response_items(data: dict[str, Any], container: str, key: str) -> list[dict[str, Any]]:
    value: Any = data.get(container, {})
    value = value.get(key, []) if isinstance(value, dict) else []
    if isinstance(value, dict):
        return [value]
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _clean_weighted_tags(
    raw_tags: list[dict[str, Any]],
    artist_name: str,
    borrowed: bool,
) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    seen: set[str] = set()
    artist_key = artist_name.casefold().strip()
    for tag in raw_tags:
        name = str(tag.get("name", "")).strip()
        key = name.casefold()
        try:
            weight = int(tag.get("count", tag.get("weight", 0)))
        except (TypeError, ValueError):
            weight = 0
        if (
            not name
            or weight < 20
            or key == artist_key
            or key in JUNK_TAGS
            or key in seen
        ):
            continue
        cleaned.append({"name": name, "weight": weight, "borrowed": borrowed})
        seen.add(key)
    return cleaned


def _listeners(data: dict[str, Any]) -> int | None:
    artist = data.get("artist", {})
    stats = artist.get("stats", {}) if isinstance(artist, dict) else {}
    value = stats.get("listeners") if isinstance(stats, dict) else None
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _artist_input(name: str, cache: LastFmDiskCache) -> ArtistInput:
    tag_data = cache.response("artist.getTopTags", artist=name)
    raw_tags = _response_items(tag_data, "toptags", "tag")
    direct_tags = _clean_weighted_tags(raw_tags, name, False)
    borrowed = len(direct_tags) < 3
    tags = list(direct_tags)
    if borrowed:
        similar_data = cache.response("artist.getSimilar", artist=name, limit=5)
        similar_artists = _response_items(similar_data, "similarartists", "artist")
        for similar in similar_artists[:5]:
            similar_name = str(similar.get("name", "")).strip()
            if not similar_name:
                continue
            similar_data = cache.response("artist.getTopTags", artist=similar_name)
            similar_tags = _response_items(similar_data, "toptags", "tag")
            borrowed_tags = _clean_weighted_tags(similar_tags, similar_name, True)
            tags.extend(
                tag for tag in borrowed_tags
                if tag["name"].casefold() != name.casefold()
            )
    info_data = cache.response("artist.getInfo", artist=name)
    return ArtistInput(
        name=name,
        tags=tags,
        listeners=_listeners(info_data),
        borrowed=borrowed,
        play_weight=1.0,
    )


def _vector_string(vector: dict[str, float]) -> str:
    return " ".join(f"{dimension}={vector[dimension]:.2f}" for dimension in DIMENSIONS)


def print_priors() -> None:
    """Print all family priors and alias counts."""
    print("id | " + " | ".join(DIMENSIONS) + " | aliases")
    for family in FAMILIES:
        dimensions = " | ".join(f"{value:.2f}" for value in family.dims)
        print(f"{family.id} | {dimensions} | {len(family.aliases)}")


def run_profiles(path: Path, cache: LastFmDiskCache) -> dict[str, Any]:
    """Resolve fixture profiles from the Last.fm disk cache and save results."""
    profiles = json.loads(path.read_text(encoding="utf-8"))
    expected = json.loads(EXPECTED_PATH.read_text(encoding="utf-8"))
    resolver = VibeResolver()
    output_profiles: list[dict[str, Any]] = []
    matched = 0
    distinct: set[str] = set()
    print(
        "profile | primary | secondary | margin | Signal% | expected_ok | "
        "top 3 families | vector"
    )
    for profile in profiles:
        artists = [_artist_input(name, cache) for name in profile["artists"]]
        result = resolver.resolve_profile(artists)
        primary = str(result["primary"])
        secondary = str(result["secondary"])
        expected_ok = primary in expected.get(profile["id"], [])
        matched += int(expected_ok)
        distinct.add(primary)
        row = {
            "id": profile["id"],
            "label": profile["label"],
            **result,
            "expected_ok": expected_ok,
        }
        output_profiles.append(row)
        families = ", ".join(
            f"{item['id']} ({item['share']:.2f})" for item in result["top_families"]
        )
        print(
            f"{profile['id']} | {primary} | {secondary} | {result['margin']:.3f} | "
            f"{result['signal_strength_pct']:.2f} | {expected_ok} | {families} | "
            f"{_vector_string(result['vector'])}"
        )
    print(f"matched {matched}/{len(profiles)}, distinct {len(distinct)}")
    if matched < 8 or len(distinct) < 6:
        misses = [
            f"{row['id']} primary={row['primary']} vector={_vector_string(row['vector'])}"
            for row in output_profiles
            if not row["expected_ok"]
        ]
        print("calibration misses: " + "; ".join(misses))
    report = {"profiles": output_profiles}
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def main() -> int:
    """Run fixture resolution or print the family-prior table."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--profiles", type=Path, default=DEFAULT_PROFILES)
    parser.add_argument("--priors", action="store_true")
    args = parser.parse_args()
    if args.priors:
        print_priors()
        return 0
    run_profiles(args.profiles, LastFmDiskCache())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
