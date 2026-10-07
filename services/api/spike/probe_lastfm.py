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
        value = value.get(key, {}) if isinstance(value, dict) else {}
    if isinstance(value, dict):
        value = [value]
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _record(
    endpoint: str,
    result: LastFmResult,
    item_count: int,
    latency_ms: float,
) -> dict[str, Any]:
    """Create a report row without names or response content."""
    return {
        "endpoint": endpoint,
        "status": result.status,
        "error_code": result.error_code,
        "latency_ms": latency_ms,
        "item_count": item_count,
    }


def _timed_call(
    endpoint: str,
    call: Any,
    item_path: tuple[str, ...],
) -> tuple[LastFmResult, dict[str, Any]]:
    started = time.perf_counter()
    result = call()
    latency = round((time.perf_counter() - started) * 1000, 2)
    return result, _record(endpoint, result, len(_items(result, item_path)), latency)


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
                "status": call.get("status") if type(call.get("status")) is int else None,
                "error_code": (
                    call.get("error_code") if type(call.get("error_code")) is int else None
                ),
                "latency_ms": (
                    call.get("latency_ms")
                    if type(call.get("latency_ms")) in (int, float)
                    else 0
                ),
                "item_count": (
                    call.get("item_count") if type(call.get("item_count")) is int else 0
                ),
            }
            for call in calls
        ],
        "usable_tag_artists_pct": round(usable / checked * 100, 2) if checked else 0.0,
        "median_cleaned_tags_per_artist": (
            statistics.median(map(len, cleaned_by_artist)) if checked else 0
        ),
        "artists_with_listeners_pct": listener_share,
        "artists_checked": checked,
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
    _, row = _timed_call("user.getInfo", lambda: client.user_get_info(username), ("user",))
    calls.append(row)
    for period in PERIODS:
        result, row = _timed_call(
            "user.getTopArtists",
            lambda period=period: client.user_top_artists(username, period),
            ("topartists", "artist"),
        )
        calls.append(row)
        for item in _items(result, ("topartists", "artist")):
            name = _artist_name(item).strip()
            key = name.casefold()
            if name and key not in seen_artists:
                artist_names.append(name)
                seen_artists.add(key)
    for period in PERIODS:
        _, row = _timed_call(
            "user.getTopTracks",
            lambda period=period: client.user_top_tracks(username, period),
            ("toptracks", "track"),
        )
        calls.append(row)
    _, row = _timed_call(
        "user.getRecentTracks",
        lambda: client.user_recent_tracks(username),
        ("recenttracks", "track"),
    )
    calls.append(row)

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

    report = build_report(calls, cleaned_by_artist, artists_with_listeners, tag_counts)
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
