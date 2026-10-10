"""Offline checks for family retrieval and recommendation scoring."""

from __future__ import annotations

import asyncio

import pytest

from app.services.catalog.clients import CatalogResult, JsonDiskCache
from app.services.catalog.family_profiles import FILM_PROFILES, GAME_PROFILES
from app.services.catalog.retrieval import (
    exact_igdb_anchor_matches,
    family_tmdb_query_groups,
    normalize_catalog_name,
    resolve_tmdb_anchor,
)
from app.services.ranking import (
    BORROWED_GAME_FAMILIES,
    RankingService,
    _allocate_family_slots,
    _article,
    _dedupe_game_rows,
    _eligible_anchor_rec,
    _eligible_movie,
    _game_anchor_plan,
    _merge_family_items,
    _mmr,
    _quality,
    _quality_with_prior,
    _rotated_anchors,
    _score_candidate,
    _source_match,
    _title_vector,
    _why,
)
from app.services.vibe.archetypes import ARCHETYPES
from app.services.vibe.genre_priors import DIMENSIONS


def _user() -> dict[str, float]:
    return {
        "energy": 0.8,
        "valence": 0.7,
        "acousticness": 0.3,
        "danceability": 0.7,
        "instrumentalness": 0.1,
        "tempo": 0.8,
        "era": 0.7,
        "mainstream": 0.6,
    }


def test_family_score_does_not_apply_pool_weight() -> None:
    item = {
        "id": 1,
        "title": "Action Film",
        "release_date": "2020-01-01",
        "vote_average": 8,
        "vote_count": 1000,
        "genre_ids": [1],
    }
    row = {"item": item, "sources": {"hip-hop": "genre"}, "shares": {"hip-hop": 0.1}}
    result = _score_candidate(
        row,
        "movie",
        _user(),
        "Neon Insomniac",
        [{"id": "hip-hop", "share": 1.0}],
        {1: "action"},
        {},
        {},
        "https://img/",
    )
    assert result["score"] > 0.3
    assert result["matched_family"]["id"] == "hip-hop"


def test_family_slot_guarantee_and_genre_diversification() -> None:
    rows = [
        {"id": "a1", "score": 0.9, "_genres": {"action"}, "matched_family": {"id": "hip-hop"}},
        {"id": "a2", "score": 0.8, "_genres": {"action"}, "matched_family": {"id": "hip-hop"}},
        {"id": "b1", "score": 0.7, "_genres": {"drama"}, "matched_family": {"id": "haryanvi"}},
        {"id": "c1", "score": 0.6, "_genres": {"comedy"}, "matched_family": {"id": "punjabi-pop"}},
        {"id": "a3", "score": 0.5, "_genres": {"action"}, "matched_family": {"id": "hip-hop"}},
    ]
    result = _mmr(rows, limit=4)
    families = [item["matched_family"]["id"] for item in result]
    assert families.count("hip-hop") >= 2
    assert "haryanvi" in families and "punjabi-pop" in families


def test_animation_excluded_for_hip_hop_but_allowed_for_kpop() -> None:
    assert "Animation" in FILM_PROFILES["hip-hop"].exclude_genres
    assert "Animation" not in FILM_PROFILES["k-pop"].exclude_genres


def test_title_era_and_mainstream_features_use_release_and_popularity() -> None:
    movie = _title_vector(
        {"release_date": "2020-05-01", "vote_count": 10**3.6},
        "movie",
        {"action"},
    )
    assert movie["era"] == pytest.approx(1.0)
    assert movie["mainstream"] == pytest.approx(0.5)
    game = _title_vector(
        {"first_release_date": 1_577_836_800, "total_rating_count": 10**2.75},
        "game",
        {"adventure"},
    )
    assert game["era"] > 0.9
    assert game["mainstream"] == pytest.approx(0.5)
    assert set(movie) == {"energy", "valence", "tempo", "era", "mainstream"}


def test_bayesian_shrinkage_favors_high_rating_at_equal_count() -> None:
    low = _quality({"vote_average": 6, "vote_count": 500}, "movie")
    high = _quality({"vote_average": 9, "vote_count": 500}, "movie")
    assert high > low
    assert _quality({"total_rating": 70, "total_rating_count": 100}, "game") == pytest.approx(0.70)


def test_family_explanations_vary_and_name_the_family() -> None:
    user = _user()
    title = {"energy": 0.8, "valence": 0.7, "tempo": 0.8, "era": 0.7, "mainstream": 0.6}
    texts = {
        _why("hip-hop", "anchor_rec", f"title-{index}", {"action"}, user, title, "8 Mile")
        for index in range(32)
    }
    assert len(texts) >= 2
    assert all("hip-hop" in text.lower() or "hip hop" in text.lower() for text in texts)
    assert all(text.endswith(".") for text in texts)


class _EmptyCatalog:
    pass


def test_family_pool_cache_and_stale_fallback(tmp_path) -> None:
    class CachedService(RankingService):
        calls = 0

        async def _retrieve_family(self, family_id, ids, anchor_seed=None):
            self.calls += 1
            return {"movies": [{"id": 1}], "games": [], "sources": {}}

    service = CachedService(_EmptyCatalog(), disk_cache=JsonDiskCache(tmp_path / "pool", ttl=3600))

    class StaleCache:
        def get(self, key):
            return None

        def get_stale(self, key):
            return {"movies": [{"id": "stale"}], "games": [], "sources": {}}

        def set(self, key, value):
            raise AssertionError("stale data should not be overwritten")

    class FailingService(RankingService):
        async def _retrieve_family(self, family_id, ids, anchor_seed=None):
            raise OSError("offline")

    fallback = FailingService(_EmptyCatalog(), disk_cache=StaleCache())

    async def exercise() -> tuple[dict, dict]:
        first = await service._family_pool("hip-hop", {})
        second = await service._family_pool("hip-hop", {})
        result = await fallback._family_pool("hip-hop", {})
        assert first == second
        assert service.calls == 1
        return first, result

    _, result = asyncio.run(exercise())
    assert result["movies"][0]["id"] == "stale"


def test_comparable_dimensions_are_the_five_requested() -> None:
    assert set(_title_vector({}, "movie", set())) == {
        "energy",
        "valence",
        "tempo",
        "era",
        "mainstream",
    }
    assert len(DIMENSIONS) == 8
    assert len(ARCHETYPES) == 8
    assert _source_match("genre", "hindi-film") == 0.2
    assert _source_match("genre", "hip-hop") == 0.2
    assert _source_match("language_genre", "hindi-film") == 0.8
    assert _source_match("keyword", "hip-hop") == 0.6
    assert _source_match("anchor_rec", "hip-hop") == 0.35
    assert _source_match("borrowed_anchor", "punjabi-pop") == 0.6
    assert _source_match("anchor", "hip-hop") == 1.0


def test_unmatched_keyword_is_not_a_degraded_reason() -> None:
    from app.services.catalog.retrieval import _keyword_errors

    no_match = CatalogResult("keyword:unused", "no_match", {"results": []})
    failure = CatalogResult("keyword:offline", None, None, "transport_error", "offline")
    assert _keyword_errors({"unused": no_match, "offline": failure}) == {"offline": "offline"}


def test_anchor_resolution_requires_exact_name_and_year() -> None:
    class FakeClient:
        async def tmdb_search_movie(self, title):
            return CatalogResult(
                "search/movie",
                200,
                {
                    "results": [
                        {"id": 1, "title": "Wrong Movie", "release_date": "2019-01-01"},
                        {"id": 2, "title": "Tar", "release_date": "2021-01-01"},
                    ]
                },
            )

    assert normalize_catalog_name("Tár") == "tar"
    assert asyncio.run(resolve_tmdb_anchor(FakeClient(), "Tar (2022)"))["id"] == 2
    assert asyncio.run(resolve_tmdb_anchor(FakeClient(), "Amadeus (1984)")) is None


def test_per_language_queries_are_individual_and_pa_first() -> None:
    profile = FILM_PROFILES["punjabi-pop"]
    ids = {
        "tmdb_genres": {"action": 1, "music": 2, "drama": 3, "animation": 4, "family": 5},
        "tmdb_keywords": {"punjabi culture": 11, "bhangra": 12},
    }
    groups = family_tmdb_query_groups(profile, ids)
    language_params = [params for source, params in groups if source == "language_genre"]
    assert [params["with_original_language"] for params in language_params[::2]] == ["pa", "hi"]
    assert all("|" not in params["with_original_language"] for params in language_params)


def test_genre_only_cap_and_anchor_copy_helpers() -> None:
    rows = [
        {
            "id": str(i),
            "score": 1 - i / 20,
            "_genres": {"drama"},
            "matched_family": {"id": "hip-hop"},
            "reason_source": "genre",
        }
        for i in range(10)
    ] + [
        {
            "id": "a",
            "score": 0.5,
            "_genres": {"crime"},
            "matched_family": {"id": "hip-hop"},
            "reason_source": "anchor",
        }
    ]
    assert sum(item["reason_source"] == "genre" for item in _mmr(rows, limit=10)) <= 2
    assert _article("action") == "An "
    assert _article("drama") == "A "
    assert not _eligible_movie(
        {"vote_count": 100, "genre_ids": [1], "original_language": "en"},
        ("hi",),
        set(),
        {1},
        100,
    )
    assert _eligible_movie(
        {"vote_count": 100, "genre_ids": [1], "original_language": "hi"},
        ("hi",),
        set(),
        {1},
        100,
    )
    assert not _eligible_movie(
        {"vote_count": 100, "genre_ids": [1, 5], "original_language": "hi"},
        ("hi",),
        {5},
        {1},
        100,
        False,
    )
    assert not _eligible_movie(
        {"vote_count": 99, "genre_ids": [1], "original_language": "hi"},
        ("hi",),
        set(),
        {1},
        100,
        False,
    )
    title_vector = {"energy": 0.7, "valence": 0.6, "tempo": 0.5, "era": 0.4, "mainstream": 0.5}
    assert "hip hop" in _why("hip-hop", "anchor", "1", set(), _user(), title_vector).lower()


def test_anchor_rotation_is_bounded_and_deterministic() -> None:
    anchors = tuple(f"Title {i}" for i in range(8))
    assert len(_rotated_anchors(anchors, b"seed")) == 4
    assert _rotated_anchors(anchors, b"seed") == _rotated_anchors(anchors, b"seed")


def test_anchor_candidates_keep_strongest_provenance() -> None:
    pools = [
        {
            "movies": [{"id": 42, "title": "Anchor Film"}],
            "sources": {"movie:42": {"hip-hop": "anchor"}},
            "anchor_names": {"movie:42": {"hip-hop": "8 Mile"}},
        },
        {
            "movies": [{"id": 42, "title": "Anchor Film"}],
            "sources": {"movie:42": {"hip-hop": "anchor_rec"}},
        },
    ]
    rows = _merge_family_items(pools, ["hip-hop", "hip-hop"], "movies")
    assert len(rows) == 1
    assert rows[0]["sources"]["hip-hop"] == "anchor"
    assert rows[0]["anchors"]["hip-hop"] == "8 Mile"


def test_anchor_rec_only_survives_for_allowed_families() -> None:
    pool = {
        "movies": [{"id": 42, "title": "Recommendation"}],
        "sources": {"movie:42": {"hip-hop": "anchor_rec"}},
    }
    assert not _merge_family_items([pool], ["hip-hop"], "movies", anchor_rec_families=set())
    allowed = _merge_family_items([pool], ["hip-hop"], "movies", anchor_rec_families={"hip-hop"})
    assert allowed[0]["sources"]["hip-hop"] == "anchor_rec"


def test_catalog_normalization_aliases_and_duplicate_resolution() -> None:
    from app.services.catalog.retrieval import match_tmdb_anchor_result

    result = CatalogResult(
        "search/movie",
        200,
        {
            "results": [
                {"id": 1, "title": "Gully Boy", "release_date": "2019-01-01", "vote_count": 4},
                {
                    "id": 2,
                    "title": "Gully Boy: The Musical",
                    "original_title": "Gully Boy",
                    "release_date": "2020-01-01",
                    "vote_count": 900,
                },
            ]
        },
    )
    assert normalize_catalog_name("  The Tár: A Story! ") == "tar a story"
    assert match_tmdb_anchor_result(result, "Gully Boy", 2019)["id"] == 2
    assert normalize_catalog_name("A Fievel's Great Adventure") == "fievel s great adventure"


def test_anchor_recommendations_obey_family_filters_and_limit() -> None:
    candidate = {"vote_average": 7.0, "original_language": "hi", "genre_ids": [1]}
    assert _eligible_anchor_rec(candidate, ("hi",), set(), {1})
    assert not _eligible_anchor_rec({**candidate, "vote_average": 6.2}, ("hi",), set(), {1})
    assert not _eligible_anchor_rec(candidate, ("pa",), set(), {1})
    assert not _eligible_anchor_rec(candidate, ("hi",), set(), {2})
    rows = [
        {
            "reason_source": "anchor_rec",
            "score": 0.8,
            "_genres": {"drama"},
            "matched_family": {"id": "a"},
        },
        {
            "reason_source": "anchor_rec",
            "score": 0.7,
            "_genres": {"crime"},
            "matched_family": {"id": "a"},
        },
        {
            "reason_source": "anchor_rec",
            "score": 0.6,
            "_genres": {"music"},
            "matched_family": {"id": "a"},
        },
        {
            "reason_source": "genre",
            "score": 0.5,
            "_genres": {"comedy"},
            "matched_family": {"id": "a"},
        },
    ]
    assert sum(item["reason_source"] == "anchor_rec" for item in _mmr(rows, 10)) <= 2


def test_family_slots_use_share_thresholds_and_largest_remainder() -> None:
    shares = [
        {"id": "a", "share": 0.50},
        {"id": "b", "share": 0.30},
        {"id": "c", "share": 0.10},
        {"id": "d", "share": 0.04},
    ]
    rows = [{"matched_family": {"id": family["id"]}} for family in shares for _ in range(10)]
    slots = _allocate_family_slots(rows, shares, 10)
    assert slots == {"a": 6, "b": 3, "c": 1}
    assert "d" not in slots


def test_high_share_families_get_direct_anchor_slots() -> None:
    rows = [
        {
            "id": "anchor",
            "score": 0.1,
            "_genres": set(),
            "matched_family": {"id": "a"},
            "reason_source": "anchor",
        },
        {
            "id": "popular",
            "score": 0.99,
            "_genres": set(),
            "matched_family": {"id": "a"},
            "reason_source": "genre",
        },
        {
            "id": "b",
            "score": 0.8,
            "_genres": set(),
            "matched_family": {"id": "b"},
            "reason_source": "genre",
        },
    ]
    picks = _mmr(rows, 2, [{"id": "a", "share": 0.7}, {"id": "b", "share": 0.3}])
    assert picks[0]["id"] == "anchor"
    more_anchors = [
        {
            "id": f"anchor-{index}",
            "score": 0.1 + index / 100,
            "_genres": {str(index)},
            "matched_family": {"id": "a"},
            "reason_source": "anchor",
        }
        for index in range(2)
    ] + [
        {
            "id": f"genre-{index}",
            "score": 0.9 - index / 100,
            "_genres": {f"genre-{index}"},
            "matched_family": {"id": "a"},
            "reason_source": "genre",
        }
        for index in range(5)
    ]
    five_slots = _mmr(more_anchors, 5, [{"id": "a", "share": 1.0}])
    assert sum(item["reason_source"] == "anchor" for item in five_slots) >= 2


def test_family_shrinkage_and_borrowed_anchor_explanation() -> None:
    expected = (100 * 8 + 150 * 6.5) / (250 * 10)
    assert _quality_with_prior(
        {"vote_average": 8, "vote_count": 100}, "movie", 150
    ) == pytest.approx(expected)
    assert _source_match("borrowed_anchor", "punjabi-pop") == 0.6
    text = _why(
        "punjabi-pop",
        "borrowed_anchor",
        "id",
        set(),
        _user(),
        {"energy": 0.5, "valence": 0.5, "tempo": 0.5, "era": 0.5, "mainstream": 0.5},
        kind="game",
        borrowed_from="hip-hop",
    )
    assert "fans of hip hop" in text.lower()
    assert BORROWED_GAME_FAMILIES["punjabi-pop"] == "hip-hop"
    assert BORROWED_GAME_FAMILIES["indian-indie"] == "alt-indie-rock"
    anchors, borrowed = _game_anchor_plan("punjabi-pop", ())
    assert anchors == GAME_PROFILES["hip-hop"].anchors
    assert borrowed == "hip-hop"
    own_anchors, borrowed = _game_anchor_plan("hip-hop", GAME_PROFILES["hip-hop"].anchors)
    assert own_anchors == GAME_PROFILES["hip-hop"].anchors
    assert borrowed is None


def test_language_and_anchor_family_uses_150_shrinkage_and_raw_anchor_quality() -> None:
    item = {"id": 81, "title": "Hindi Film", "vote_average": 8, "vote_count": 100}
    families = [{"id": "hindi-film", "share": 1.0}]
    common = (_user(), "Neon Insomniac", families, {}, {}, {}, "https://image/")
    language_row = {"item": item, "sources": {"hindi-film": "language_genre"}}
    anchor_row = {"item": item, "sources": {"hindi-film": "anchor"}}
    language = _score_candidate(language_row, "movie", *common)
    anchor = _score_candidate(anchor_row, "movie", *common)
    assert language["_components"]["quality"] == pytest.approx(0.71)
    assert anchor["_components"]["quality"] == pytest.approx(0.8)
    assert anchor["_components"]["fit"] == 1.0


def test_igdb_anchor_matches_alternative_titles_and_ranks_duplicates() -> None:
    result = CatalogResult(
        "games",
        200,
        [
            {
                "id": 1,
                "name": "Wrong title",
                "alternative_names": [{"name": "Def Jam: Fight for New York"}],
                "total_rating_count": 10,
            },
            {
                "id": 2,
                "name": "Def Jam Fight for NY",
                "alternative_names": [],
                "total_rating_count": 90,
            },
        ],
    )
    matches = exact_igdb_anchor_matches(result, "Def Jam: Fight for NY")
    assert [item["id"] for item in matches] == [2, 1]


def test_game_ports_dedupe_by_rating_count_and_show_earliest_year() -> None:
    earlier = 1_262_304_000
    later = 1_577_836_800
    rows = [
        {
            "item": {
                "id": 1,
                "name": "Game: Definitive Edition",
                "total_rating_count": 20,
                "first_release_date": later,
            },
            "sources": {"a": "genre"},
        },
        {
            "item": {
                "id": 2,
                "name": "Game Definitive Edition",
                "total_rating_count": 50,
                "first_release_date": earlier,
            },
            "sources": {"b": "anchor"},
        },
    ]
    result = _dedupe_game_rows(rows)
    assert len(result) == 1
    assert result[0]["item"]["id"] == 2
    assert result[0]["item"]["first_release_date"] == earlier
