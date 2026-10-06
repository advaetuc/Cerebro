"""Probe Spotify endpoint availability and privacy-safe field coverage."""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from typing import Any

import httpx

from spike.spotify_auth import get_access_token

API_BASE = "https://api.spotify.com/v1"
OUTPUT_PATH = Path(__file__).resolve().parent / "out" / "endpoint_report.json"
RANGES = ("short_term", "medium_term", "long_term")
TRACK_FIELDS = ("preview_url", "popularity", "duration_ms", "album.release_date", "explicit", "artists[].id")
ARTIST_FIELDS = ("genres", "popularity", "followers")
BANNED_KEYS = {"name", "access_token", "refresh_token", "user_id", "user_ids", "track_name", "artist_name"}


def _nested_present(value: Any, field: str) -> bool:
    current = value
    for part in field.split("."):
        if part.endswith("[]"):
            current = current if isinstance(current, list) else []
            key = part[:-2]
            return any(isinstance(item, dict) and item.get(key) is not None for item in current)
        if not isinstance(current, dict) or part not in current:
            return False
        current = current[part]
    return current is not None


def field_presence(items: list[dict[str, Any]], fields: tuple[str, ...], nonempty: set[str] | None = None) -> dict[str, bool]:
    """Summarize whether response items contain each requested field."""
    nonempty = nonempty or set()
    result: dict[str, bool] = {}
    for field in fields:
        present = [_nested_present(item, field) for item in items]
        if field in nonempty:
            present = [flag and bool(_field_value(item, field)) for item, flag in zip(items, present)]
        result[field] = any(present)
    return result


def _field_value(value: dict[str, Any], field: str) -> Any:
    current: Any = value
    for part in field.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def build_report(records: list[dict[str, Any]], unique_track_count: int) -> dict[str, Any]:
    """Build a report containing only endpoint metrics and presence flags."""
    safe_records = []
    for record in records:
        safe = {key: record[key] for key in ("endpoint", "status", "latency_ms", "item_count") if key in record}
        if "error_message" in record:
            safe["error_message"] = str(record["error_message"])[:200]
        if "field_presence" in record:
            safe["field_presence"] = dict(record["field_presence"])
        safe_records.append(safe)
    report = {"calls": safe_records, "unique_track_count": unique_track_count}
    if _contains_banned_key(report):
        raise ValueError("Report contains a banned key")
    return report


def _contains_banned_key(value: Any) -> bool:
    if isinstance(value, dict):
        return any(key.lower() in BANNED_KEYS or _contains_banned_key(item) for key, item in value.items())
    if isinstance(value, list):
        return any(_contains_banned_key(item) for item in value)
    return False


def _items(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    data = payload.get("items", payload.get("artists", []))
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    return []


def _request(client: httpx.Client, endpoint: str, params: dict[str, Any] | None = None, fields: tuple[str, ...] | None = None, nonempty: set[str] | None = None) -> tuple[dict[str, Any], Any]:
    started = time.perf_counter()
    response = client.get(f"{API_BASE}{endpoint}", params=params)
    latency = round((time.perf_counter() - started) * 1000, 2)
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    items = _items(payload)
    record: dict[str, Any] = {
        "endpoint": re.sub(r"/(audio-features|audio-analysis)/[^/]+$", r"/\1/{track_id}", endpoint),
        "status": response.status_code,
        "latency_ms": latency,
        "item_count": len(items),
    }
    if response.is_error:
        message = payload.get("error", {}).get("message", "") if isinstance(payload, dict) else ""
        record["error_message"] = str(message)[:200]
    if fields is not None and response.is_success:
        record["field_presence"] = field_presence(items, fields, nonempty)
    return record, payload


def run_probe(access_token: str) -> dict[str, Any]:
    """Run the configured Spotify calls and return the privacy-safe report."""
    headers = {"Authorization": f"Bearer {access_token}"}
    records: list[dict[str, Any]] = []
    track_ids: set[str] = set()
    with httpx.Client(headers=headers, timeout=30) as client:
        record, _ = _request(client, "/me")
        records.append(record)
        top_tracks: dict[str, list[dict[str, Any]]] = {}
        for term in RANGES:
            record, payload = _request(client, "/me/top/tracks", {"time_range": term, "limit": 50}, TRACK_FIELDS)
            records.append(record)
            top_tracks[term] = _items(payload)
            track_ids.update(str(item["id"]) for item in top_tracks[term] if item.get("id"))
        for term in RANGES:
            record, _ = _request(client, "/me/top/artists", {"time_range": term, "limit": 50}, ARTIST_FIELDS, {"genres"})
            records.append(record)
        record, payload = _request(client, "/me/player/recently-played", {"limit": 50})
        records.append(record)
        recent_items = _items(payload)
        track_ids.update(str(item.get("track", {}).get("id")) for item in recent_items if item.get("track", {}).get("id"))
        seed_id = next((str(item["id"]) for term in RANGES for item in top_tracks[term] if item.get("id")), None)
        if seed_id:
            for endpoint in (f"/audio-features/{seed_id}", f"/audio-analysis/{seed_id}"):
                record, _ = _request(client, endpoint)
                records.append(record)
            record, _ = _request(client, "/recommendations", {"seed_tracks": seed_id})
            records.append(record)
        else:
            for endpoint in ("/audio-features/{top_track_id}", "/audio-analysis/{top_track_id}", "/recommendations"):
                records.append({"endpoint": endpoint, "status": 0, "latency_ms": 0, "item_count": 0, "error_message": "Skipped: no top-track ID returned"})
        record, _ = _request(client, "/search", {"type": "artist", "q": "radiohead", "limit": 10})
        records.append(record)
    report = build_report(records, len(track_ids))
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("endpoint | status | items | ms")
    for record in records:
        print(f"{record['endpoint']} | {record['status']} | {record['item_count']} | {record['latency_ms']}")
    print(f"Unique tracks gathered: {len(track_ids)}")
    return report


def main() -> int:
    """Run the Spotify endpoint probe."""
    try:
        run_probe(get_access_token())
    except (httpx.HTTPError, OSError, RuntimeError, ValueError) as error:
        print(f"Probe could not complete: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
