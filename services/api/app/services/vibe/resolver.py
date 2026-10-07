"""T2/T3 Vibe Resolver for Last.fm tags and genre-family priors."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from .genre_priors import DIMENSIONS, FAMILY_BY_ID
from .normalize import normalize_and_classify

T2_WEIGHT = 0.5
T3_WEIGHT = 0.35
TAG_ONLY_SIGNAL_CAP = 65.0
DEFAULT_VECTOR = {dimension: 0.5 for dimension in DIMENSIONS}

ARCHETYPES = {
    "Neon Insomniac": (0.82, 0.25, 0.15, 0.82, 0.25, 0.78, 0.55, 0.70),
    "Golden Hour Dreamer": (0.52, 0.84, 0.78, 0.56, 0.20, 0.48, 0.62, 0.55),
    "Static Saint": (0.24, 0.30, 0.70, 0.18, 0.87, 0.22, 0.54, 0.27),
    "Velvet Rebel": (0.56, 0.28, 0.24, 0.46, 0.18, 0.53, 0.28, 0.50),
    "Solar Sprinter": (0.90, 0.84, 0.18, 0.86, 0.08, 0.91, 0.78, 0.73),
    "Hollow Wanderer": (0.25, 0.24, 0.82, 0.22, 0.35, 0.28, 0.48, 0.30),
    "Chrome Romantic": (0.68, 0.86, 0.20, 0.91, 0.06, 0.70, 0.78, 0.78),
    "Echo Archivist": (0.42, 0.48, 0.56, 0.36, 0.38, 0.40, 0.88, 0.22),
}


@dataclass(frozen=True)
class ArtistInput:
    """Input evidence for one artist in a profile."""

    name: str
    tags: list[dict[str, Any]] = field(default_factory=list)
    listeners: int | None = None
    borrowed: bool = False
    play_weight: float = 1.0


def mainstream_from_listeners(listeners: int | float | None) -> float | None:
    """Map listener counts linearly in log space from 10^4..10^7 to 0..1."""
    if listeners is None or listeners <= 0:
        return None
    value = (math.log10(listeners) - 4.0) / 3.0
    return min(1.0, max(0.0, value))


def blend_mainstream(data_value: float | None, prior_value: float) -> float:
    """Blend listener-derived mainstream signal with its family prior."""
    if data_value is None:
        return prior_value
    return 0.7 * data_value + 0.3 * prior_value


def _cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    numerator = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return numerator / (left_norm * right_norm)


def _decade_value(year: int) -> float:
    return min(1.0, max(0.0, (year - 1950) / 70.0))


class VibeResolver:
    """Resolve weighted artist tags into CVV-8 and a blueprint archetype."""

    def resolve_artist(self, artist: ArtistInput) -> dict[str, Any]:
        """Resolve one artist to a prior-weighted vector and confidence."""
        family_weights: dict[str, float] = {}
        decade_weights: list[tuple[int, float]] = []
        total_tag_weight = 0.0
        mapped_tag_weight = 0.0
        for tag in artist.tags:
            raw_name = str(tag.get("name", ""))
            try:
                raw_weight = max(0.0, float(tag.get("weight", tag.get("count", 0))))
            except (TypeError, ValueError):
                raw_weight = 0.0
            is_borrowed = bool(tag.get("borrowed", artist.borrowed))
            weight = raw_weight * (0.5 if is_borrowed else 1.0)
            total_tag_weight += weight
            normalized = normalize_and_classify(raw_name)
            if normalized.kind == "genre" and normalized.family_id:
                family_weights[normalized.family_id] = (
                    family_weights.get(normalized.family_id, 0.0) + weight
                )
                mapped_tag_weight += weight
            elif normalized.kind == "decade" and normalized.decade_year is not None:
                decade_weights.append((normalized.decade_year, weight))

        family_total = sum(family_weights.values())
        distribution = (
            {family_id: weight / family_total for family_id, weight in family_weights.items()}
            if family_total
            else {}
        )
        vector = dict(DEFAULT_VECTOR)
        confidence = {dimension: 0.0 for dimension in DIMENSIONS}
        family_prior = dict(DEFAULT_VECTOR)
        tag_coverage = mapped_tag_weight / total_tag_weight if total_tag_weight else 0.0
        if distribution:
            for index, dimension in enumerate(DIMENSIONS):
                tag_projection = sum(
                    FAMILY_BY_ID[family_id].dims[index] * share
                    for family_id, share in distribution.items()
                )
                family_prior[dimension] = sum(
                    FAMILY_BY_ID[family_id].dims[index]
                    for family_id in distribution
                ) / len(distribution)
                vector[dimension] = (
                    T2_WEIGHT * tag_projection + T3_WEIGHT * family_prior[dimension]
                ) / (T2_WEIGHT + T3_WEIGHT)
                confidence[dimension] = min(
                    1.0, T3_WEIGHT + T2_WEIGHT * tag_coverage
                )
        if decade_weights:
            total_decade_weight = sum(weight for _, weight in decade_weights)
            vector["era"] = sum(
                _decade_value(year) * weight for year, weight in decade_weights
            ) / total_decade_weight
            confidence["era"] = 0.25
        listener_value = mainstream_from_listeners(artist.listeners)
        if listener_value is not None:
            vector["mainstream"] = blend_mainstream(
                listener_value, family_prior["mainstream"]
            )
            confidence["mainstream"] = (
                0.7 * T2_WEIGHT + 0.3 * T3_WEIGHT if distribution else T2_WEIGHT
            )
        return {
            "name": artist.name,
            "vector": vector,
            "confidence": confidence,
            "family_distribution": distribution,
            "mapped_tag_weight": mapped_tag_weight,
            "total_tag_weight": total_tag_weight,
            "borrowed": artist.borrowed,
            "play_weight": max(0.0, artist.play_weight),
        }

    def resolve_profile(self, artists: list[ArtistInput]) -> dict[str, Any]:
        """Combine artist signals with square-root play weights."""
        resolved = [self.resolve_artist(artist) for artist in artists]
        dimension_values: dict[str, float] = {}
        dimension_confidence: dict[str, float] = {}
        for dimension in DIMENSIONS:
            contributors = [
                (artist, math.sqrt(artist["play_weight"]))
                for artist in resolved
                if artist["confidence"][dimension] > 0 and artist["play_weight"] > 0
            ]
            denominator = sum(weight for _, weight in contributors)
            dimension_values[dimension] = (
                sum(item["vector"][dimension] * weight for item, weight in contributors)
                / denominator
                if denominator
                else 0.5
            )
            dimension_confidence[dimension] = (
                sum(item["confidence"][dimension] * weight for item, weight in contributors)
                / denominator
                if denominator
                else 0.0
            )

        total_tag_weight = sum(item["total_tag_weight"] for item in resolved)
        mapped_tag_weight = sum(item["mapped_tag_weight"] for item in resolved)
        coverage = mapped_tag_weight / total_tag_weight if total_tag_weight else 0.0
        total_play_weight = sum(math.sqrt(item["play_weight"]) for item in resolved)
        borrowed_weight = sum(
            math.sqrt(item["play_weight"])
            for item in resolved
            if item["borrowed"]
        )
        borrowed_share = borrowed_weight / total_play_weight if total_play_weight else 0.0
        artist_factor = min(1.0, math.sqrt(len(resolved) / 10.0))
        signal_strength = min(
            TAG_ONLY_SIGNAL_CAP,
            100.0 * (T2_WEIGHT + T3_WEIGHT) * coverage * artist_factor
            * (1.0 - 0.5 * borrowed_share),
        )
        family_totals: dict[str, float] = {}
        for item in resolved:
            play_weight = math.sqrt(item["play_weight"])
            for family_id, share in item["family_distribution"].items():
                family_totals[family_id] = family_totals.get(family_id, 0.0) + share * play_weight
        family_denominator = sum(family_totals.values())
        top_families = [
            {"id": family_id, "share": family_weight / family_denominator}
            for family_id, family_weight in sorted(
                family_totals.items(), key=lambda entry: (-entry[1], entry[0])
            )[:3]
        ] if family_denominator else []
        vector_tuple = tuple(dimension_values[dimension] for dimension in DIMENSIONS)
        archetype = max(
            ARCHETYPES,
            key=lambda name: _cosine(vector_tuple, ARCHETYPES[name]),
        )
        return {
            "vector": dimension_values,
            "confidence": dimension_confidence,
            "signal_strength_pct": round(signal_strength, 2),
            "archetype": archetype,
            "top_families": top_families,
            "artists": resolved,
        }
