"""Print and save a human-reviewable family profile table."""

from __future__ import annotations

from pathlib import Path

from app.services.catalog.family_profiles import (
    FAMILY_LABELS,
    FILM_PROFILES,
    GAME_PROFILES,
)

API_ROOT = Path(__file__).resolve().parents[1]
OUTPUT = API_ROOT.parents[1] / "docs" / "family_profiles_review.md"


def _join(values: tuple[str, ...]) -> str:
    return ", ".join(values) if values else "—"


def render_table() -> str:
    """Render every family with its film and game profile fields."""
    lines = [
        "| family | film genres | keywords | languages | excludes | film anchors | "
        "game genres/themes | game anchors |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for family_id, film in FILM_PROFILES.items():
        game = GAME_PROFILES[family_id]
        lines.append(
            "| "
            + " | ".join(
                (
                    FAMILY_LABELS[family_id],
                    _join(film.tmdb_genres),
                    _join(film.tmdb_keywords),
                    _join(film.original_languages),
                    _join(film.exclude_genres),
                    _join(film.anchors),
                    f"{_join(game.igdb_genres)}; {_join(game.igdb_themes)}",
                    _join(game.anchors),
                )
            )
            + " |"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    """Print the profile table and save a review copy in docs."""
    table = render_table()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(table, encoding="utf-8")
    print(table, end="")


if __name__ == "__main__":
    main()
