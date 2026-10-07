"""Blueprint archetype centroids and weighted matching."""

from __future__ import annotations

import math
from dataclasses import dataclass

from .genre_priors import DIMENSIONS, FAMILIES


@dataclass(frozen=True)
class Archetype:
    """Store a named, weighted eight-dimension archetype centroid."""

    name: str
    centroid: tuple[float, ...]
    weights: tuple[float, ...]


def _weights(tendencies: set[str]) -> tuple[float, ...]:
    return tuple(1.0 if dimension in tendencies else 0.4 for dimension in DIMENSIONS)


ARCHETYPES = (
    Archetype(
        "Neon Insomniac", (.80, .30, .20, .80, .20, .75, .70, .60),
        _weights({"energy", "valence", "danceability"}),
    ),
    Archetype(
        "Golden Hour Dreamer", (.50, .80, .70, .50, .15, .45, .55, .60),
        _weights({"energy", "valence", "acousticness"}),
    ),
    Archetype(
        "Static Saint", (.25, .30, .50, .25, .85, .25, .50, .35),
        _weights({"energy", "valence", "instrumentalness"}),
    ),
    Archetype(
        "Velvet Rebel", (.60, .30, .20, .45, .15, .60, .25, .60),
        _weights({"energy", "valence", "acousticness", "era"}),
    ),
    Archetype(
        "Solar Sprinter", (.90, .85, .20, .75, .10, .90, .70, .70),
        _weights({"energy", "valence", "tempo"}),
    ),
    Archetype(
        "Hollow Wanderer", (.25, .25, .80, .25, .30, .30, .45, .40),
        _weights({"energy", "valence", "acousticness"}),
    ),
    Archetype(
        "Chrome Romantic", (.65, .85, .10, .90, .10, .70, .75, .75),
        _weights({"valence", "danceability", "acousticness"}),
    ),
    Archetype(
        "Echo Archivist", (.45, .55, .50, .45, .35, .45, .15, .25),
        _weights({"era", "mainstream"}),
    ),
)

POPULATION_MEAN = tuple(
    sum(family.dims[index] for family in FAMILIES) / len(FAMILIES)
    for index in range(len(DIMENSIONS))
)
POPULATION_STD = tuple(
    math.sqrt(
        sum((family.dims[index] - POPULATION_MEAN[index]) ** 2 for family in FAMILIES)
        / len(FAMILIES)
    )
    for index in range(len(DIMENSIONS))
)


def _standardize(values: tuple[float, ...]) -> tuple[float, ...]:
    return tuple(
        (value - POPULATION_MEAN[index]) / POPULATION_STD[index]
        if POPULATION_STD[index]
        else 0.0
        for index, value in enumerate(values)
    )


def _weighted_cosine(
    left: tuple[float, ...], right: tuple[float, ...], weights: tuple[float, ...]
) -> float:
    numerator = sum(
        weights[index] * left[index] * right[index] for index in range(len(weights))
    )
    left_norm = math.sqrt(
        sum(weights[index] * left[index] ** 2 for index in range(len(weights)))
    )
    right_norm = math.sqrt(
        sum(weights[index] * right[index] ** 2 for index in range(len(weights)))
    )
    return numerator / (left_norm * right_norm) if left_norm and right_norm else 0.0


def match_archetypes(vector: dict[str, float]) -> dict[str, object]:
    """Return the two nearest standardized archetypes and their score margin."""
    values = tuple(vector[dimension] for dimension in DIMENSIONS)
    standardized_user = _standardize(values)
    scores = sorted(
        (
            _weighted_cosine(
                standardized_user,
                _standardize(archetype.centroid),
                archetype.weights,
            ),
            archetype.name,
        )
        for archetype in ARCHETYPES
    )
    scores.reverse()
    margin = scores[0][0] - scores[1][0]
    return {
        "primary": scores[0][1],
        "secondary": scores[1][1],
        "margin": margin,
        "low_margin": margin < 0.05,
        "scores": {name: score for score, name in scores},
    }
