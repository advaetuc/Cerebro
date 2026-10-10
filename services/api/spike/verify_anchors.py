"""Check configured catalog anchors against their public catalog records."""

from __future__ import annotations

import asyncio

from app.services.catalog.clients import CatalogClients
from app.services.catalog.family_profiles import FILM_PROFILES, GAME_PROFILES
from app.services.catalog.retrieval import parse_anchor, resolve_igdb_anchor, resolve_tmdb_anchor


async def run() -> None:
    """Resolve all configured anchors and print a compact verification table."""
    clients = CatalogClients()
    print("family | anchor | resolved title | year | language | ok")
    try:
        for family_id, film in FILM_PROFILES.items():
            for anchor in film.anchors:
                title, _ = parse_anchor(anchor)
                item = await resolve_tmdb_anchor(clients, anchor)
                print(
                    f"{family_id} | {anchor} | {item.get('title', '') if item else ''} | "
                    f"{str(item.get('release_date', ''))[:4] if item else ''} | "
                    f"{item.get('original_language', '') if item else ''} | {bool(item)}"
                )
        for family_id, game in GAME_PROFILES.items():
            for anchor in game.anchors:
                item, _ = await resolve_igdb_anchor(clients, anchor)
                print(
                    f"{family_id} | {anchor} | "
                    f"{item.get('name', '') if item else ''} | — | — | {bool(item)}"
                )
    finally:
        await clients.close()


def main() -> None:
    """Run anchor verification."""
    asyncio.run(run())


if __name__ == "__main__":
    main()
