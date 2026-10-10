"""Film and game retrieval profiles for music genre families."""

from __future__ import annotations

from dataclasses import dataclass

from app.services.vibe.genre_priors import FAMILIES


@dataclass(frozen=True)
class FilmProfile:
    """Describe film catalog intent for one music family."""

    tmdb_genres: tuple[str, ...] = ()
    tmdb_keywords: tuple[str, ...] = ()
    original_languages: tuple[str, ...] = ()
    exclude_genres: tuple[str, ...] = ("Animation", "Family")
    anchors: tuple[str, ...] = ()


@dataclass(frozen=True)
class GameProfile:
    """Describe game catalog intent for one music family."""

    igdb_genres: tuple[str, ...] = ()
    igdb_themes: tuple[str, ...] = ()
    anchors: tuple[str, ...] = ()


FAMILY_LABELS = {family.id: family.id.replace("-", " ").title() for family in FAMILIES}
FAMILY_LABELS.update({"r-and-b-soul": "R&B / Soul", "k-pop": "K-pop"})

_FILM_INTENT: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "hindi-film": (("Drama", "Music", "Romance"), ("bollywood", "hindi cinema")),
    "indian-indie": (("Drama", "Music"), ("indian independent film",)),
    "punjabi-pop": (("Action", "Music", "Drama"), ("punjabi culture", "bhangra")),
    "haryanvi": (("Drama", "Music"), ("haryanvi culture",)),
    "desi-hip-hop": (("Crime", "Drama", "Music"), ("indian hip hop", "street culture")),
    "hip-hop": (("Crime", "Drama", "Music"), ("hip hop", "rap music")),
    "trap": (("Crime", "Music", "Thriller"), ("trap music",)),
    "boom-bap": (("Crime", "Documentary", "Drama"), ("underground hip hop",)),
    "pop": (("Comedy", "Music", "Romance"), ("pop music",)),
    "electropop": (("Science Fiction", "Music", "Romance"), ("electronic music",)),
    "k-pop": (("Comedy", "Music", "Romance"), ("k-pop", "korean pop")),
    "r-and-b-soul": (("Drama", "Music", "Romance"), ("soul music",)),
    "classic-rock": (("Drama", "Music"), ("classic rock",)),
    "hard-rock": (("Action", "Drama", "Music"), ("hard rock",)),
    "alt-indie-rock": (("Drama", "Music"), ("independent film", "indie rock")),
    "grunge": (("Drama", "Music"), ("grunge", "1990s")),
    "prog-rock": (("Adventure", "Drama", "Music"), ("progressive rock",)),
    "folk-rock": (("Drama", "Music", "Western"), ("folk music",)),
    "metal": (("Action", "Horror", "Music"), ("heavy metal",)),
    "nu-metal": (("Action", "Drama", "Music"), ("nu metal",)),
    "house-techno": (("Music", "Science Fiction"), ("club culture", "techno")),
    "dubstep": (("Action", "Music", "Science Fiction"), ("dubstep",)),
    "idm": (("Documentary", "Drama", "Science Fiction"), ("experimental music",)),
    "downtempo-trip-hop": (("Crime", "Drama", "Music"), ("trip hop",)),
    "ambient-chillout": (("Documentary", "Drama", "Science Fiction"), ("ambient music",)),
    "jazz": (("Drama", "Music"), ("jazz",)),
    "free-jazz": (("Documentary", "Drama", "Music"), ("free jazz",)),
    "bebop": (("Drama", "Music"), ("bebop",)),
    "classical": (("Drama", "History", "Music"), ("classical music",)),
    "baroque": (("Drama", "History", "Music"), ("baroque",)),
    "contemporary-classical": (("Drama", "Music", "Science Fiction"), ("contemporary classical",)),
    "piano": (("Drama", "Music", "Romance"), ("piano music",)),
    "folk": (("Drama", "Music", "Western"), ("folk music",)),
    "americana": (("Drama", "Music", "Western"), ("americana",)),
    "country": (("Drama", "Music", "Romance"), ("country music",)),
    "singer-songwriter": (("Drama", "Music", "Romance"), ("singer-songwriter",)),
    "blues": (("Drama", "Music"), ("blues music",)),
    "disco-dance": (("Comedy", "Music", "Romance"), ("disco", "dance music")),
    "soundtrack": (("Adventure", "Fantasy", "Music"), ("film score",)),
    "world": (("Documentary", "Drama", "Music"), ("world music",)),
    "experimental": (("Documentary", "Drama", "Science Fiction"), ("experimental film",)),
}

_GAME_INTENT: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "hindi-film": (("Adventure", "Indie"), ("Narrative", "Comedy")),
    "indian-indie": (("Adventure", "Indie"), ("Narrative", "Atmospheric")),
    "punjabi-pop": (("Music", "Fighting"), ("Party", "Action")),
    "haryanvi": (("Adventure", "Indie"), ("Action", "Narrative")),
    "desi-hip-hop": (("Music", "Indie"), ("Action", "Crime")),
    "hip-hop": (("Music", "Action"), ("Action", "Crime")),
    "trap": (("Shooter", "Music"), ("Action", "Cyberpunk")),
    "boom-bap": (("Strategy", "Music"), ("Narrative", "Retro")),
    "pop": (("Music", "Platform"), ("Comedy", "Party")),
    "electropop": (("Music", "Racing"), ("Cyberpunk", "Action")),
    "k-pop": (("Music", "Party"), ("Comedy", "Co-operative")),
    "r-and-b-soul": (("Music", "Adventure"), ("Narrative", "Romance")),
    "classic-rock": (("Music", "Adventure"), ("Retro", "Action")),
    "hard-rock": (("Shooter", "Action"), ("Action", "Survival")),
    "alt-indie-rock": (("Adventure", "Indie"), ("Narrative", "Atmospheric")),
    "grunge": (("Adventure", "Indie"), ("Dark", "Atmospheric")),
    "prog-rock": (("Strategy", "Adventure"), ("Fantasy", "Narrative")),
    "folk-rock": (("Adventure", "Indie"), ("Fantasy", "Open world")),
    "metal": (("Shooter", "Action"), ("Dark", "Action")),
    "nu-metal": (("Shooter", "Action"), ("Cyberpunk", "Action")),
    "house-techno": (("Music", "Racing"), ("Party", "Cyberpunk")),
    "dubstep": (("Shooter", "Music"), ("Action", "Cyberpunk")),
    "idm": (("Puzzle", "Adventure"), ("Abstract", "Atmospheric")),
    "downtempo-trip-hop": (("Adventure", "Puzzle"), ("Atmospheric", "Narrative")),
    "ambient-chillout": (("Adventure", "Puzzle"), ("Atmospheric", "Open world")),
    "jazz": (("Music", "Adventure"), ("Narrative", "Retro")),
    "free-jazz": (("Music", "Puzzle"), ("Abstract", "Experimental")),
    "bebop": (("Music", "Adventure"), ("Retro", "Narrative")),
    "classical": (("Strategy", "Puzzle"), ("Historical", "Turn-based strategy")),
    "baroque": (("Strategy", "Puzzle"), ("Historical", "Turn-based strategy")),
    "contemporary-classical": (("Puzzle", "Adventure"), ("Atmospheric", "Narrative")),
    "piano": (("Music", "Puzzle"), ("Atmospheric", "Narrative")),
    "folk": (("Adventure", "Indie"), ("Fantasy", "Open world")),
    "americana": (("Adventure", "Role-playing (RPG)"), ("Western", "Open world")),
    "country": (("Adventure", "Music"), ("Western", "Narrative")),
    "singer-songwriter": (("Adventure", "Indie"), ("Narrative", "Atmospheric")),
    "blues": (("Music", "Adventure"), ("Retro", "Narrative")),
    "disco-dance": (("Music", "Party"), ("Party", "Co-operative")),
    "soundtrack": (("Adventure", "Role-playing (RPG)"), ("Fantasy", "Narrative")),
    "world": (("Adventure", "Indie"), ("Historical", "Open world")),
    "experimental": (("Puzzle", "Indie"), ("Abstract", "Experimental")),
}

_LANGUAGES = {
    "punjabi-pop": ("pa", "hi"),
    "haryanvi": ("hi",),
    "hindi-film": ("hi",),
    "desi-hip-hop": ("hi", "pa"),
    "indian-indie": ("hi",),
}
_NO_EXCLUDES = {"pop", "k-pop", "electropop", "disco-dance", "soundtrack"}
_FILM_ANCHORS = {
    "hindi-film": ("Dil Se.. (1998)", "Gully Boy (2019)"),
    "jazz": ("Whiplash (2014)", "Round Midnight (1986)"),
    "metal": ("This Is Spinal Tap (1984)", "Metal: A Headbanger's Journey (2005)"),
    "hip-hop": ("8 Mile (2002)", "Do the Right Thing (1989)"),
    "country": ("Coal Miner's Daughter (1980)", "Walk the Line (2005)"),
    "disco-dance": ("Saturday Night Fever (1977)", "The Last Days of Disco (1998)"),
}
_GAME_ANCHORS = {
    "hip-hop": ("Def Jam: Fight for NY", "Def Jam Vendetta"),
    "classical": ("Civilization VI", "Civilization V"),
    "ambient-chillout": ("Minecraft", "Journey"),
    "soundtrack": ("Final Fantasy VII", "Final Fantasy X"),
    "boom-bap": ("PaRappa the Rapper", "PaRappa the Rapper 2"),
}

FILM_PROFILES = {
    family.id: FilmProfile(
        *_FILM_INTENT.get(family.id, (("Drama", "Music"), ())),
        _LANGUAGES.get(family.id, ()),
        () if family.id in _NO_EXCLUDES else ("Animation", "Family"),
        _FILM_ANCHORS.get(family.id, ()),
    )
    for family in FAMILIES
}
GAME_PROFILES = {
    family.id: GameProfile(
        *_GAME_INTENT.get(family.id, (("Adventure", "Indie"), ("Narrative",))),
        _GAME_ANCHORS.get(family.id, ()),
    )
    for family in FAMILIES
}
