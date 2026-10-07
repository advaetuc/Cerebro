"""Probe Last.fm endpoints and summarize privacy-safe signal coverage."""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

from spike.lastfm_client import LastFmClient, LastFmResult

OUTPUT_PATH = Path(__file__).resolve().parent / "out" / "lastfm_report.json"
PERIODS = ("1month", "6month", "overall")
JUNK_TAGS = frozenset(
    {"seen live", "favorites", "favourite", "favourites", "albums i own", "spotify"}
)
ENDPOINTS = frozenset(
    {
        "user.getInfo",
        "user.getTopArtists",
        "user.getTopTracks",
        "user.getRecentTracks",
        "artist.getTopTags",
        "artist.getInfo",
    }
)


def clean_tags(raw_tags: list[dict[str, Any]], artist_name: str) -> list[str]:
    """Filter weak, self-named, and junk tags and deduplicate the remainder."""
    cleaned: list[str] = []
    seen: set[str] = set()
    artist_key = artist_name.strip().casefold()
    for tag in raw_tags:
        name = str(tag.get("name", "")).strip()
        key = name.casefold()
        try:
            weight = int(tag.get("count", tag.get("weight", 0)))
        except (TypeError, ValueError):
            weight = 0
        if not name or weight < 20 or key == artist_key or key in JUNK_TAGS or key in seen:
            continue
        cleaned.append(name)
        seen.add(key)
    return cleaned


def resolve_tags_with_fallback(
    artist_name: str,
    raw_tags: list[dict[str, Any]],
    similar_tag_sets: list[tuple[str, list[dict[str, Any]]]],
) -> tuple[dict[str, float], bool]:
    """Return cleaned tag weights and borrow only when direct coverage is low."""
    direct_names = clean_tags(raw_tags, artist_name)
    resolved: dict[str, float] = {}
    seen: set[str] = set()
    for tag in raw_tags:
        name = str(tag.get("name", "")).strip()
        key = name.casefold()
        if name in direct_names and key not in seen:
            resolved[name] = _tag_weight(tag)
            seen.add(key)
    if len(seen) >= 3:
        return resolved, False
    for similar_name, similar_tags in similar_tag_sets:
        valid_names = clean_tags(similar_tags, similar_name)
        for tag in similar_tags:
            name = str(tag.get("name", "")).strip()
            key = name.casefold()
            if (
                name in valid_names
                and key != artist_name.casefold()
                and key not in seen
            ):
                resolved[name] = _tag_weight(tag) * 0.5
                seen.add(key)
    return resolved, True


def _tag_weight(tag: dict[str, Any]) -> float:
    try:
        return float(tag.get("count", tag.get("weight", 0)))
    except (TypeError, ValueError):
        return 0.0


def _items(result: LastFmResult, path: tuple[str, ...]) -> list[dict[str, Any]]:
    value: Any = result.data
    for key in path:
        if not isinstance(value, dict) or key not in value:
            return []
        value = value[key]
    if isinstance(value, dict):
        value = [value] if value else []
    if not isinstance(value, list):
        return []
    items = [item for item in value if isinstance(item, dict)]
    if path == ("recenttracks", "track"):
        items = [item for item in items if not _is_now_playing(item)]
    return items


def _is_now_playing(item: dict[str, Any]) -> bool:
    marker = item.get("@attr", {})
    return isinstance(marker, dict) and str(marker.get("nowplaying", "")).casefold() == "true"


def _response_metadata(result: LastFmResult, path: tuple[str, ...]) -> dict[str, Any]:
    container: Any = result.data
    container_path = path if len(path) == 1 else path[:-1]
    for key in container_path:
        container = container.get(key, {}) if isinstance(container, dict) else {}
    attrs = container.get("@attr", {}) if isinstance(container, dict) else {}
    raw_total = attrs.get("total") if isinstance(attrs, dict) else None
    try:
        raw_total = int(raw_total) if raw_total is not None else None
    except (TypeError, ValueError):
        raw_total = None
    return {
        "response_keys": sorted(result.data),
        "container_keys": sorted(container) if isinstance(container, dict) else [],
        "raw_total": raw_total,
        "query_param_names": list(result.query_param_names),
    }


def _record(
    endpoint: str,
    result: LastFmResult,
    item_count: int,
    latency_ms: float,
    item_path: tuple[str, ...],
) -> dict[str, Any]:
    """Create a report row without names or response content."""
    return {
        "endpoint": endpoint,
        "status": result.status,
        "error_code": result.error_code,
        "latency_ms": latency_ms,
        "item_count": item_count,
        **_response_metadata(result, item_path),
    }


def _timed_call(
    endpoint: str,
    call: Any,
    item_path: tuple[str, ...],
) -> tuple[LastFmResult, dict[str, Any]]:
    started = time.perf_counter()
    result = call()
    latency = round((time.perf_counter() - started) * 1000, 2)
    return result, _record(endpoint, result, len(_items(result, item_path)), latency, item_path)


def _artist_name(item: dict[str, Any]) -> str:
    artist = item.get("artist", item)
    return str(artist.get("name", "")) if isinstance(artist, dict) else ""


def _tag_names(result: LastFmResult) -> list[dict[str, Any]]:
    return _items(result, ("toptags", "tag"))


def build_report(
    calls: list[dict[str, Any]],
    cleaned_by_artist: list[list[str]],
    artists_with_listeners: int,
    tag_counts: Counter[str],
    user_playcount: int | None = None,
) -> dict[str, Any]:
    """Build a report containing metrics and aggregate tags without identities."""
    checked = len(cleaned_by_artist)
    usable = sum(len(tags) >= 3 for tags in cleaned_by_artist)
    listener_share = round(artists_with_listeners / checked * 100, 2) if checked else 0.0
    return {
        "calls": [
            {
                "endpoint": (
                    call.get("endpoint") if call.get("endpoint") in ENDPOINTS else "unknown"
                ),
                "status": (
                    call.get("status")
                    if isinstance(call.get("status"), int)
                    and not isinstance(call.get("status"), bool)
                    else None
                ),
                "error_code": (
                    call.get("error_code")
                    if isinstance(call.get("error_code"), int)
                    and not isinstance(call.get("error_code"), bool)
                    else None
                ),
                "latency_ms": (
                    call.get("latency_ms")
                    if isinstance(call.get("latency_ms"), int | float)
                    and not isinstance(call.get("latency_ms"), bool)
                    else 0
                ),
                "item_count": (
                    call.get("item_count")
                    if isinstance(call.get("item_count"), int)
                    and not isinstance(call.get("item_count"), bool)
                    else 0
                ),
                "response_keys": call.get("response_keys", []),
                "container_keys": call.get("container_keys", []),
                "raw_total": call.get("raw_total"),
                "query_param_names": call.get("query_param_names", []),
            }
            for call in calls
        ],
        "usable_tag_artists_pct": round(usable / checked * 100, 2) if checked else 0.0,
        "median_cleaned_tags_per_artist": (
            statistics.median(map(len, cleaned_by_artist)) if checked else 0
        ),
        "artists_with_listeners_pct": listener_share,
        "artists_checked": checked,
        "user_playcount": user_playcount,
        "aggregated_top_tags": [
            {"tag": tag, "count": count}
            for tag, count in tag_counts.most_common(60)
        ],
    }


def _profile_metrics(artists: list[dict[str, Any]]) -> dict[str, Any]:
    checked = len(artists)
    direct_usable = sum(artist["direct_tag_count"] >= 3 for artist in artists)
    fallback_usable = sum(artist["cleaned_tag_count"] >= 3 for artist in artists)
    return {
        "direct_usable_pct": round(direct_usable / checked * 100, 2) if checked else 0.0,
        "fallback_usable_pct": round(fallback_usable / checked * 100, 2) if checked else 0.0,
        "artists_zero_tags_after_fallback": [
            artist["name"] for artist in artists if artist["cleaned_tag_count"] == 0
        ],
        "median_cleaned_tags": (
            statistics.median(artist["cleaned_tag_count"] for artist in artists)
            if checked
            else 0
        ),
    }


def _build_vocabulary(
    artists: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    tag_documents: Counter[str] = Counter()
    tag_weights: Counter[str] = Counter()
    display_names: dict[str, str] = {}
    for artist in artists:
        for tag, weight in artist["tags"].items():
            key = tag.casefold()
            tag_documents[key] += 1
            tag_weights[key] += weight
            display_names.setdefault(key, tag)
    singleton_count = sum(count == 1 for count in tag_documents.values())
    vocabulary = [
        {
            "tag": display_names[key],
            "distinct_artist_count": count,
            "weight": round(tag_weights[key], 2),
        }
        for key, count in sorted(
            tag_documents.items(), key=lambda item: (-item[1], item[0])
        )
        if count > 1
    ][:150]
    return vocabulary, singleton_count


def _response_items(result: LastFmResult, container: str, item: str) -> list[dict[str, Any]]:
    return _items(result, (container, item))


def _similar_artist_names(result: LastFmResult) -> list[str]:
    return [
        str(item.get("name", "")).strip()
        for item in _response_items(result, "similarartists", "artist")
        if item.get("name")
    ]


def _probe_fixture_artist(
    artist_name: str,
    client: LastFmClient,
    calls: list[dict[str, Any]],
) -> dict[str, Any]:
    tags_result, row = _timed_call(
        "artist.getTopTags", lambda: client.artist_top_tags(artist_name), ("toptags", "tag")
    )
    calls.append(row)
    raw_tags = _tag_names(tags_result)
    direct_tags = clean_tags(raw_tags, artist_name)
    _, row = _timed_call(
        "artist.getInfo", lambda: client.artist_get_info(artist_name), ("artist",)
    )
    calls.append(row)
    similar_result, row = _timed_call(
        "artist.getSimilar",
        lambda: client.artist_similar(artist_name, limit=5),
        ("similarartists", "artist"),
    )
    calls.append(row)
    similar_names = _similar_artist_names(similar_result)[:5] if len(direct_tags) < 3 else []
    similar_tag_sets: list[tuple[str, list[dict[str, Any]]]] = []
    for similar_name in similar_names:
        similar_result, row = _timed_call(
            "artist.getTopTags",
            lambda similar_name=similar_name: client.artist_top_tags(similar_name),
            ("toptags", "tag"),
        )
        calls.append(row)
        similar_tag_sets.append((similar_name, _tag_names(similar_result)))
    resolved, borrowed = resolve_tags_with_fallback(
        artist_name, raw_tags, similar_tag_sets
    )
    return {
        "name": artist_name,
        "direct_tag_count": len(direct_tags),
        "cleaned_tag_count": len(resolved),
        "borrowed": borrowed,
        "tags": resolved,
    }


def _profile_report(profiles: list[dict[str, Any]], client: LastFmClient) -> dict[str, Any]:
    calls: list[dict[str, Any]] = []
    resolved_artists: dict[str, dict[str, Any]] = {}
    profile_rows: list[dict[str, Any]] = []
    for profile in profiles:
        rows: list[dict[str, Any]] = []
        for artist_name in profile["artists"]:
            artist_key = artist_name.casefold()
            if artist_key not in resolved_artists:
                resolved_artists[artist_key] = _probe_fixture_artist(
                    artist_name, client, calls
                )
            rows.append(resolved_artists[artist_key])
        profile_rows.append(
            {
                "id": profile["id"],
                "label": profile["label"],
                **_profile_metrics(rows),
                "artists": [
                    {
                        "name": row["name"],
                        "direct_tag_count": row["direct_tag_count"],
                        "cleaned_tag_count": row["cleaned_tag_count"],
                        "borrowed": row["borrowed"],
                    }
                    for row in rows
                ],
            }
        )

    unique_artists = list(resolved_artists.values())
    vocabulary, singleton_count = _build_vocabulary(unique_artists)
    global_metrics = _profile_metrics(unique_artists)
    return {
        "profiles": profile_rows,
        "global": global_metrics,
        "vocabulary": vocabulary,
        "singleton_tag_count": singleton_count,
        "calls": calls,
    }


def run_profiles(path: Path, client: LastFmClient) -> dict[str, Any]:
    """Probe profiles from a JSON fixture and write aggregate tag coverage."""
    profiles = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(profiles, list):
        raise ValueError("Profile fixture must be a JSON list")
    for profile in profiles:
        if (
            not isinstance(profile, dict)
            or not isinstance(profile.get("id"), str)
            or not isinstance(profile.get("label"), str)
            or not isinstance(profile.get("artists"), list)
            or not all(isinstance(name, str) and name.strip() for name in profile["artists"])
        ):
            raise ValueError("Each profile must have an id, label, and artist name list")
    report = _profile_report(profiles, client)
    output_path = OUTPUT_PATH.parent / "fixtures_report.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("profile | label | direct % | fallback % | median tags")
    for profile in report["profiles"]:
        print(
            f"{profile['id']} | {profile['label']} | {profile['direct_usable_pct']} | "
            f"{profile['fallback_usable_pct']} | {profile['median_cleaned_tags']}"
        )
    print(
        json.dumps(
            report["global"] | {"singleton_tag_count": report["singleton_tag_count"]},
            indent=2,
        )
    )
    return report


def run_probe(username: str, client: LastFmClient) -> dict[str, Any]:
    """Run the Last.fm profile and artist probes and write the aggregate report."""
    calls: list[dict[str, Any]] = []
    artist_names: list[str] = []
    seen_artists: set[str] = set()
    info_result, row = _timed_call(
        "user.getInfo", lambda: client.user_get_info(username), ("user",)
    )
    calls.append(row)
    info = info_result.data.get("user", {})
    try:
        user_playcount = (
            int(info["playcount"])
            if isinstance(info, dict) and "playcount" in info
            else None
        )
    except (TypeError, ValueError):
        user_playcount = None
    has_list_items = False
    for period in PERIODS:
        result, row = _timed_call(
            "user.getTopArtists",
            lambda period=period: client.user_top_artists(username, period),
            ("topartists", "artist"),
        )
        calls.append(row)
        artists = _items(result, ("topartists", "artist"))
        has_list_items = has_list_items or bool(artists)
        for item in artists:
            name = _artist_name(item).strip()
            key = name.casefold()
            if name and key not in seen_artists:
                artist_names.append(name)
                seen_artists.add(key)
    for period in PERIODS:
        result, row = _timed_call(
            "user.getTopTracks",
            lambda period=period: client.user_top_tracks(username, period),
            ("toptracks", "track"),
        )
        calls.append(row)
        has_list_items = has_list_items or bool(_items(result, ("toptracks", "track")))
    result, row = _timed_call(
        "user.getRecentTracks",
        lambda: client.user_recent_tracks(username),
        ("recenttracks", "track"),
    )
    calls.append(row)
    has_list_items = has_list_items or bool(_items(result, ("recenttracks", "track")))

    if user_playcount == 0 or not has_list_items:
        report = build_report(calls, [], 0, Counter(), user_playcount)
        OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print("No listening data for this user")
        return report

    cleaned_by_artist: list[list[str]] = []
    tag_counts: Counter[str] = Counter()
    artists_with_listeners = 0
    for artist in artist_names[:100]:
        tags_result, tag_row = _timed_call(
            "artist.getTopTags",
            lambda artist=artist: client.artist_top_tags(artist),
            ("toptags", "tag"),
        )
        calls.append(tag_row)
        cleaned = clean_tags(_tag_names(tags_result), artist)
        cleaned_by_artist.append(cleaned)
        tag_counts.update(tag.casefold() for tag in cleaned)
        info_result, info_row = _timed_call(
            "artist.getInfo",
            lambda artist=artist: client.artist_get_info(artist),
            ("artist",),
        )
        calls.append(info_row)
        info = info_result.data.get("artist", {})
        stats = info.get("stats", {}) if isinstance(info, dict) else {}
        listeners = stats.get("listeners") if isinstance(stats, dict) else None
        if listeners not in (None, "", 0, "0"):
            artists_with_listeners += 1

    report = build_report(
        calls, cleaned_by_artist, artists_with_listeners, tag_counts, user_playcount
    )
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("endpoint | status | error | items | ms")
    for call in calls:
        print(
            f"{call['endpoint']} | {call['status']} | {call['error_code']} | "
            f"{call['item_count']} | {call['latency_ms']}"
        )
    print(json.dumps({key: value for key, value in report.items() if key != "calls"}, indent=2))
    return report


def main() -> int:
    """Run the command-line Last.fm endpoint probe."""
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--user")
    source.add_argument("--profiles", type=Path)
    args = parser.parse_args()
    try:
        client = LastFmClient()
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2
    try:
        if args.profiles is not None:
            run_profiles(args.profiles, client)
        else:
            run_probe(args.user, client)
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
