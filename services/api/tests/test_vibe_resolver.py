"""Offline tests for tag normalization and Vibe Resolver calculations."""

from app.services.vibe.normalize import normalize_and_classify, normalize_tag
from app.services.vibe.resolver import (
    ArtistInput,
    VibeResolver,
    blend_mainstream,
    mainstream_from_listeners,
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
