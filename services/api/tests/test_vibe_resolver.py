"""Offline tests for tag normalization and Vibe Resolver calculations."""

import json
from pathlib import Path

import pytest

from app.services.vibe.archetypes import ARCHETYPES, POPULATION_MEAN, POPULATION_STD
from app.services.vibe.normalize import normalize_and_classify, normalize_tag
from app.services.vibe.resolver import (
    ArtistInput,
    VibeResolver,
    blend_mainstream,
    mainstream_from_listeners,
)
from spike.run_resolver import (
    CACHE_DIR,
    DEFAULT_PROFILES,
    LastFmDiskCache,
    run_profiles,
)


def test_alias_normalization_maps_vocabulary_variants() -> None:
    assert normalize_tag("Kpop") == "kpop"
    assert normalize_and_classify("Kpop").family_id == "k-pop"
    assert normalize_and_classify("hip hop").family_id == "hip-hop"
    assert normalize_and_classify("desi hip hop").family_id == "desi-hip-hop"


def test_origin_tags_never_change_dimensions() -> None:
    resolver = VibeResolver()
    base = ArtistInput("artist", [{"name": "bollywood", "weight": 40}])
    with_origin = ArtistInput(
        "artist",
        [{"name": "bollywood", "weight": 40}, {"name": "Indian", "weight": 100}],
    )
    assert resolver.resolve_artist(base)["vector"] == resolver.resolve_artist(with_origin)["vector"]
    assert normalize_and_classify("Hindi").kind == "origin"


def test_origin_tags_count_toward_mapping_quality_only() -> None:
    resolver = VibeResolver()
    genre = resolver.resolve_artist(ArtistInput("artist", [{"name": "bollywood", "weight": 40}]))
    origin = resolver.resolve_artist(
        ArtistInput(
            "artist",
            [{"name": "bollywood", "weight": 40}, {"name": "indian", "weight": 100}],
        )
    )
    assert origin["vector"] == genre["vector"]
    assert origin["coverage_tag_weight"] > genre["coverage_tag_weight"]


def test_decade_tag_sets_era_dimension() -> None:
    result = VibeResolver().resolve_artist(
        ArtistInput("artist", [{"name": "80s", "weight": 25}])
    )
    assert result["vector"]["era"] == 30 / 70
    assert result["confidence"]["era"] == 0.25


def test_tag_only_signal_is_capped_at_65() -> None:
    artists = [
        ArtistInput(f"artist-{index}", [{"name": "pop", "weight": 100}])
        for index in range(10)
    ]
    assert VibeResolver().resolve_profile(artists)["signal_strength_pct"] == 65.0


def test_play_weight_is_damped_with_square_root() -> None:
    result = VibeResolver().resolve_profile(
        [
            ArtistInput("metal", [{"name": "metal", "weight": 100}], play_weight=1),
            ArtistInput("ambient", [{"name": "ambient", "weight": 100}], play_weight=9),
        ]
    )
    assert abs(result["vector"]["energy"] - (0.91 + 3 * 0.22) / 4) < 1e-12


def test_mainstream_mapping_endpoints_and_blend() -> None:
    assert mainstream_from_listeners(10_000) == 0.0
    assert mainstream_from_listeners(10_000_000) == 1.0
    assert blend_mainstream(1.0, 0.0) == 0.7


def test_archetype_matching_uses_family_population_standardization() -> None:
    assert len(ARCHETYPES) == 8
    assert len(POPULATION_MEAN) == len(POPULATION_STD) == 8
    assert all(value > 0 for value in POPULATION_STD)


def test_cached_fixture_profiles_meet_archetype_coverage() -> None:
    if not CACHE_DIR.exists() or not any(CACHE_DIR.glob("*.json")):
        pytest.skip("Last.fm response cache is absent; cached calibration cannot run")
    expected = json.loads(
        (Path(__file__).parents[1] / "spike" / "fixtures" / "expected_archetypes.json")
        .read_text(encoding="utf-8")
    )
    report = run_profiles(DEFAULT_PROFILES, LastFmDiskCache())
    rows = report["profiles"]
    matched = sum(row["primary"] in expected[row["id"]] for row in rows)
    distinct = {row["primary"] for row in rows}
    misses = [
        f"{row['id']}={row['primary']} vector={row['vector']}"
        for row in rows
        if row["primary"] not in expected[row["id"]]
    ]
    assert matched >= 8, f"matched {matched}/10; misses: {misses}"
    assert len(distinct) >= 6, (
        f"used {len(distinct)} distinct archetypes; misses: {misses}"
    )
