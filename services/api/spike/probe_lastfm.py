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
                    if isinstance(call.get("latency_ms"), (int, float))
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
    parser.add_argument("--user", required=True)
    args = parser.parse_args()
    try:
        client = LastFmClient()
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2
    try:
        run_probe(args.user, client)
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
