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
    "k-pop": ("ko",),
}
_NO_EXCLUDES = {"pop", "k-pop", "electropop", "disco-dance", "soundtrack"}
_FILM_ANCHORS = {
    "hindi-film": (
        "Pyaasa (1957)",
        "Guide (1965)",
        "Sholay (1975)",
        "Mughal-E-Azam (1960)",
        "Dil Se.. (1998)",
        "Dilwale Dulhania Le Jayenge (1995)",
        "Rockstar (2011)",
        "Aashiqui 2 (2013)",
    ),
    "indian-indie": (
        "Wake Up Sid (2009)",
        "Masaan (2015)",
        "The Lunchbox (2013)",
        "Tamasha (2015)",
        "Zindagi Na Milegi Dobara (2011)",
        "Udaan (2010)",
    ),
    "punjabi-pop": (
        "Jatt & Juliet (2012)",
        "Qismat (2018)",
        "Chal Mera Putt (2019)",
        "Udta Punjab (2016)",
        "Jab We Met (2007)",
        "Singh Is Kinng (2008)",
    ),
    "haryanvi": ("Dangal (2016)", "Sultan (2016)", "NH10 (2015)"),
    "desi-hip-hop": ("Gully Boy (2019)",),
    "hip-hop": (
        "8 Mile (2002)",
        "Do the Right Thing (1989)",
        "Straight Outta Compton (2015)",
        "Hustle & Flow (2005)",
        "Boyz n the Hood (1991)",
        "Wild Style (1983)",
    ),
    "trap": ("Dope (2015)", "Spring Breakers (2012)", "Hustle & Flow (2005)"),
    "boom-bap": (
        "Wild Style (1983)",
        "Beat Street (1984)",
        "Juice (1992)",
        "Menace II Society (1993)",
    ),
    "pop": (
        "Pitch Perfect (2012)",
        "Mamma Mia! (2008)",
        "A Star Is Born (2018)",
        "Yesterday (2019)",
    ),
    "electropop": (
        "Tron: Legacy (2010)",
        "Scott Pilgrim vs. the World (2010)",
        "Drive (2011)",
        "Spring Breakers (2012)",
    ),
    "k-pop": (
        "KPop Demon Hunters (2025)",
        "BLACKPINK: Light Up the Sky (2020)",
        "Burn the Stage: The Movie (2018)",
        "Parasite (2019)",
        "Train to Busan (2016)",
    ),
    "r-and-b-soul": (
        "Ray (2004)",
        "Dreamgirls (2006)",
        "Love & Basketball (2000)",
        "Brown Sugar (2002)",
    ),
    "classic-rock": (
        "Almost Famous (2000)",
        "School of Rock (2003)",
        "Bohemian Rhapsody (2018)",
        "Dazed and Confused (1993)",
        "The Doors (1991)",
    ),
    "hard-rock": ("This Is Spinal Tap (1984)", "Rock Star (2001)", "Airheads (1994)"),
    "alt-indie-rock": (
        "Juno (2007)",
        "Garden State (2004)",
        "Sing Street (2016)",
        "High Fidelity (2000)",
        "Scott Pilgrim vs. the World (2010)",
    ),
    "grunge": ("Singles (1992)", "Pearl Jam Twenty (2011)", "Kurt Cobain: Montage of Heck (2015)"),
    "prog-rock": ("Pink Floyd: The Wall (1982)", "Tommy (1975)"),
    "folk-rock": (
        "Inside Llewyn Davis (2013)",
        "A Complete Unknown (2024)",
        "Into the Wild (2007)",
    ),
    "metal": (
        "Metal: A Headbanger's Journey (2005)",
        "Sound of Metal (2019)",
        "Lords of Chaos (2018)",
        "This Is Spinal Tap (1984)",
    ),
    "nu-metal": (
        "Metal: A Headbanger's Journey (2005)",
        "Sound of Metal (2019)",
        "Lords of Chaos (2018)",
        "This Is Spinal Tap (1984)",
    ),
    "house-techno": (
        "Eden (2014)",
        "Human Traffic (1999)",
        "Berlin Calling (2008)",
        "Tron: Legacy (2010)",
    ),
    "dubstep": ("Tron: Legacy (2010)", "Spring Breakers (2012)"),
    "idm": ("Pi (1998)", "Requiem for a Dream (2000)", "Under the Skin (2013)"),
    "downtempo-trip-hop": (
        "Lost in Translation (2003)",
        "Trainspotting (1996)",
        "Ghost in the Shell (1995)",
    ),
    "ambient-chillout": (
        "Blade Runner 2049 (2017)",
        "Arrival (2016)",
        "Solaris (1972)",
        "Lost in Translation (2003)",
    ),
    "jazz": (
        "Whiplash (2014)",
        "La La Land (2016)",
        "Round Midnight (1986)",
        "Bird (1988)",
        "Miles Ahead (2015)",
    ),
    "free-jazz": ("Bird (1988)", "Round Midnight (1986)", "Mo' Better Blues (1990)"),
    "bebop": ("Bird (1988)", "Round Midnight (1986)", "Whiplash (2014)"),
    "classical": (
        "Amadeus (1984)",
        "The Pianist (2002)",
        "Tar (2022)",
        "Immortal Beloved (1994)",
        "Shine (1996)",
    ),
    "baroque": ("Amadeus (1984)", "Farinelli (1994)", "Tous les matins du monde (1991)"),
    "contemporary-classical": (
        "Arrival (2016)",
        "There Will Be Blood (2007)",
        "Under the Skin (2013)",
    ),
    "piano": ("The Piano (1993)", "The Pianist (2002)", "Shine (1996)"),
    "folk": (
        "Inside Llewyn Davis (2013)",
        "Into the Wild (2007)",
        "Once (2007)",
        "A Complete Unknown (2024)",
    ),
    "americana": (
        "O Brother, Where Art Thou? (2000)",
        "Crazy Heart (2009)",
        "Tender Mercies (1983)",
    ),
    "country": (
        "Walk the Line (2005)",
        "Coal Miner's Daughter (1980)",
        "Crazy Heart (2009)",
        "Nashville (1975)",
        "Country Strong (2010)",
    ),
    "singer-songwriter": (
        "Once (2007)",
        "Begin Again (2013)",
        "A Star Is Born (2018)",
        "Inside Llewyn Davis (2013)",
    ),
    "blues": (
        "Cadillac Records (2008)",
        "The Blues Brothers (1980)",
        "Crossroads (1986)",
        "Ray (2004)",
    ),
    "disco-dance": (
        "Saturday Night Fever (1977)",
        "The Last Days of Disco (1998)",
        "Boogie Nights (1997)",
        "Studio 54 (2018)",
    ),
    "soundtrack": (
        "Interstellar (2014)",
        "Inception (2010)",
        "Gladiator (2000)",
        "The Lord of the Rings: The Fellowship of the Ring (2001)",
    ),
    "world": (
        "Buena Vista Social Club (1999)",
        "Slumdog Millionaire (2008)",
        "Black Orpheus (1959)",
    ),
    "experimental": ("Eraserhead (1977)", "Mulholland Drive (2001)", "Enter the Void (2009)"),
}
_GAME_ANCHORS = {
    "hip-hop": (
        "Grand Theft Auto: San Andreas",
        "Def Jam: Fight for NY",
        "Need for Speed: Underground 2",
        "Tony Hawk's Pro Skater 2",
    ),
    "desi-hip-hop": (
        "Grand Theft Auto: San Andreas",
        "Def Jam: Fight for NY",
        "Need for Speed: Underground 2",
        "Tony Hawk's Pro Skater 2",
    ),
    "trap": (
        "Grand Theft Auto: San Andreas",
        "Def Jam: Fight for NY",
        "Need for Speed: Underground 2",
        "Tony Hawk's Pro Skater 2",
    ),
    "boom-bap": (
        "Grand Theft Auto: San Andreas",
        "Def Jam: Fight for NY",
        "Need for Speed: Underground 2",
        "Tony Hawk's Pro Skater 2",
    ),
    "pop": (
        "Just Dance 2022",
        "Beat Saber",
        "Sayonara Wild Hearts",
        "Hatsune Miku: Project DIVA Future Tone",
        "Grand Theft Auto: Vice City",
    ),
    "disco-dance": (
        "Just Dance 2022",
        "Beat Saber",
        "Sayonara Wild Hearts",
        "Hatsune Miku: Project DIVA Future Tone",
        "Grand Theft Auto: Vice City",
    ),
    "k-pop": (
        "Just Dance 2022",
        "Beat Saber",
        "Sayonara Wild Hearts",
        "Hatsune Miku: Project DIVA Future Tone",
        "Grand Theft Auto: Vice City",
    ),
    "electropop": (
        "Just Dance 2022",
        "Beat Saber",
        "Sayonara Wild Hearts",
        "Hatsune Miku: Project DIVA Future Tone",
        "Grand Theft Auto: Vice City",
    ),
    "r-and-b-soul": ("Cuphead", "Persona 5 Royal", "Katamari Damacy", "Bayonetta", "Mafia III"),
    "jazz": ("Cuphead", "Persona 5 Royal", "Katamari Damacy", "Bayonetta", "Mafia III"),
    "bebop": ("Cuphead", "Persona 5 Royal", "Katamari Damacy", "Bayonetta", "Mafia III"),
    "free-jazz": ("Cuphead", "Persona 5 Royal", "Katamari Damacy", "Bayonetta", "Mafia III"),
    "blues": ("Cuphead", "Persona 5 Royal", "Katamari Damacy", "Bayonetta", "Mafia III"),
    "classic-rock": ("Guitar Hero III: Legends of Rock", "Rock Band", "Tony Hawk's Pro Skater 2"),
    "hard-rock": ("Guitar Hero III: Legends of Rock", "Rock Band", "Tony Hawk's Pro Skater 2"),
    "prog-rock": ("Guitar Hero III: Legends of Rock", "Rock Band", "Tony Hawk's Pro Skater 2"),
    "grunge": ("Guitar Hero III: Legends of Rock", "Rock Band", "Tony Hawk's Pro Skater 2"),
    "alt-indie-rock": ("Guitar Hero III: Legends of Rock", "Rock Band", "Tony Hawk's Pro Skater 2"),
    "metal": ("Metal: Hellsinger", "Brutal Legend", "Guitar Hero: Metallica"),
    "nu-metal": ("Metal: Hellsinger", "Brutal Legend", "Guitar Hero: Metallica"),
    "house-techno": (
        "Tetris Effect: Connected",
        "Rez Infinite",
        "Hotline Miami",
        "Beat Saber",
        "WipEout Omega Collection",
        "Cyberpunk 2077",
    ),
    "dubstep": (
        "Tetris Effect: Connected",
        "Rez Infinite",
        "Hotline Miami",
        "Beat Saber",
        "WipEout Omega Collection",
        "Cyberpunk 2077",
    ),
    "idm": (
        "Tetris Effect: Connected",
        "Rez Infinite",
        "Hotline Miami",
        "Beat Saber",
        "WipEout Omega Collection",
        "Cyberpunk 2077",
    ),
    "downtempo-trip-hop": ("Journey", "Flower", "Hyper Light Drifter", "Fez", "Inside"),
    "ambient-chillout": ("Journey", "Flower", "Hyper Light Drifter", "Fez", "Inside"),
    "experimental": ("Journey", "Flower", "Hyper Light Drifter", "Fez", "Inside"),
    "classical": ("Ori and the Blind Forest", "Gris", "To the Moon", "Sid Meier's Civilization VI"),
    "baroque": ("Ori and the Blind Forest", "Gris", "To the Moon", "Sid Meier's Civilization VI"),
    "contemporary-classical": (
        "Ori and the Blind Forest",
        "Gris",
        "To the Moon",
        "Sid Meier's Civilization VI",
    ),
    "piano": ("Ori and the Blind Forest", "Gris", "To the Moon", "Sid Meier's Civilization VI"),
    "folk": (
        "Red Dead Redemption 2",
        "Red Dead Redemption",
        "Kentucky Route Zero",
        "Outer Wilds",
        "Stardew Valley",
    ),
    "folk-rock": (
        "Red Dead Redemption 2",
        "Red Dead Redemption",
        "Kentucky Route Zero",
        "Outer Wilds",
        "Stardew Valley",
    ),
    "americana": (
        "Red Dead Redemption 2",
        "Red Dead Redemption",
        "Kentucky Route Zero",
        "Outer Wilds",
        "Stardew Valley",
    ),
    "country": (
        "Red Dead Redemption 2",
        "Red Dead Redemption",
        "Kentucky Route Zero",
        "Outer Wilds",
        "Stardew Valley",
    ),
    "singer-songwriter": (
        "Red Dead Redemption 2",
        "Red Dead Redemption",
        "Kentucky Route Zero",
        "Outer Wilds",
        "Stardew Valley",
    ),
    "soundtrack": ("Final Fantasy VII", "NieR: Automata", "Journey"),
    "world": ("Raji: An Ancient Epic", "Ghost of Tsushima", "Journey"),
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
