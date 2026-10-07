"""Normalize and classify Last.fm tags for the Vibe Resolver."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

from .genre_priors import FAMILIES

TagKind = Literal["genre", "origin", "decade", "noise"]
_NON_WORD = re.compile(r"[^a-z0-9]+")
_DECADE = re.compile(r"^(?:(19)?(50|60|70|80|90)|(2000|2010|2020))s$")

NOISE_TAGS = frozenset(
    {
        "all",
        "female-vocalists",
        "male-vocalists",
        "seen-live",
        "favorites",
        "favourite",
        "favourites",
        "albums-i-own",
        "spotify",
        "youtube",
        "my-favorites",
        "awesome",
        "beautiful",
        "love",
        "music",
        "listened-to",
        "favorite-artists",
    }
)
ORIGIN_TAGS = frozenset(
    {
        "indian",
        "india",
        "hindi",
        "punjabi",
        "punjab",
        "mumbai",
        "british",
        "britain",
        "uk",
        "american",
        "usa",
        "canadian",
        "canada",
        "korean",
        "korea",
        "asia",
        "kerala",
        "mohali",
        "german",
        "germany",
        "english",
        "french",
        "japanese",
        "japan",
        "swedish",
        "australian",
        "latin",
        "desi",
    }
)


@dataclass(frozen=True)
class NormalizedTag:
    """Hold a normalized tag and its classification."""

    value: str
    kind: TagKind
    family_id: str | None = None
    decade_year: int | None = None


def normalize_tag(value: str) -> str:
    """Lowercase a tag and unify punctuation, spaces, and hyphens."""
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    ascii_value = "".join(char for char in decomposed if not unicodedata.combining(char))
    return _NON_WORD.sub("-", ascii_value).strip("-")


def _decade_year(value: str) -> int | None:
    match = _DECADE.fullmatch(value)
    if not match:
        return None
    decade = match.group(2)
    if decade:
        return 1900 + int(decade)
    return int(match.group(4))


def build_alias_map() -> dict[str, str]:
    """Build normalized aliases from the curated tag vocabulary."""
    aliases: dict[str, str] = {}
    for family in FAMILIES:
        for alias in (family.id, *family.aliases):
            aliases[normalize_tag(alias)] = family.id
    aliases.update(
        {
            "kpop": "k-pop",
            "hiphop": "hip-hop",
            "hip-hop-music": "hip-hop",
            "desi-hiphop": "desi-hip-hop",
            "indie-india": "indian-indie",
            "indian-indie": "indian-indie",
            "r-and-b": "r-and-b-soul",
            "rnb": "r-and-b-soul",
            "trip-hop": "downtempo-trip-hop",
            "trip-hop-music": "downtempo-trip-hop",
        }
    )
    return aliases


TAG_TO_FAMILY = build_alias_map()


def normalize_and_classify(value: str) -> NormalizedTag:
    """Normalize and classify a genre, origin, decade, or noise tag."""
    normalized = normalize_tag(value)
    if normalized in NOISE_TAGS or not normalized:
        return NormalizedTag(normalized, "noise")
    decade_year = _decade_year(normalized)
    if decade_year is not None:
        return NormalizedTag(normalized, "decade", decade_year=decade_year)
    if normalized in ORIGIN_TAGS:
        return NormalizedTag(normalized, "origin")
    family_id = TAG_TO_FAMILY.get(normalized)
    if family_id:
        return NormalizedTag(normalized, "genre", family_id=family_id)
    return NormalizedTag(normalized, "noise")
