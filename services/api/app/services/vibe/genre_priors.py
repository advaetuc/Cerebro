"""Hand-curated T3 genre-family priors for the CVV-8 dimensions."""

from __future__ import annotations

from dataclasses import dataclass

DIMENSIONS = (
    "energy",
    "valence",
    "acousticness",
    "danceability",
    "instrumentalness",
    "tempo",
    "era",
    "mainstream",
)


@dataclass(frozen=True)
class GenreFamily:
    """Describe aliases and an eight-dimensional family centroid."""

    id: str
    aliases: tuple[str, ...]
    dims: tuple[float, float, float, float, float, float, float, float]


FAMILIES = (
    GenreFamily(
        "hindi-film", ("hindi film", "bollywood", "filmi", "hindi soundtrack"),
        (.62, .72, .35, .72, .08, .65, .55, .78),
    ),
    GenreFamily(
        "indian-indie", ("hindie", "indian indie", "indie india", "indian independent"),
        (.48, .62, .55, .50, .25, .48, .68, .38),
    ),
    GenreFamily(
        "punjabi-pop", ("punjabi pop", "bhangra", "punjabi rap", "punjabi dance"),
        (.78, .78, .18, .86, .06, .82, .83, .58),
    ),
    GenreFamily(
        "haryanvi", ("haryanvi", "haryanvi music", "haryanvi folk"),
        (.83, .64, .20, .75, .08, .80, .87, .48),
    ),
    GenreFamily(
        "desi-hip-hop", ("desi hip-hop", "desi hip hop", "indian hip-hop", "indian rap"),
        (.76, .42, .20, .73, .09, .72, .89, .72),
    ),
    GenreFamily(
        "hip-hop", ("hip-hop", "hip hop", "hip-hop music", "rap", "gangsta rap"),
        (.74, .43, .17, .77, .05, .70, .62, .87),
    ),
    GenreFamily(
        "trap", ("trap", "trap music", "mumble rap"),
        (.81, .38, .10, .80, .10, .72, .94, .88),
    ),
    GenreFamily(
        "boom-bap",
        ("boom bap", "boom-bap", "underground hip-hop", "east coast rap", "conscious hip-hop"),
        (.67, .45, .22, .62, .10, .58, .48, .50),
    ),
    GenreFamily(
        "pop", ("pop", "power pop", "teen pop", "synth-pop"),
        (.68, .76, .25, .78, .05, .70, .83, .96),
    ),
    GenreFamily(
        "electropop", ("electropop", "electro-pop", "dance pop", "indietronica"),
        (.76, .75, .08, .84, .08, .78, .92, .86),
    ),
    GenreFamily(
        "k-pop", ("k-pop", "kpop", "korean pop", "k-pop girl group"),
        (.78, .80, .16, .91, .06, .84, .98, .83),
    ),
    GenreFamily(
        "r-and-b-soul", ("r&b", "rnb", "r and b", "soul", "neo soul", "contemporary r&b"),
        (.52, .58, .30, .68, .11, .55, .64, .76),
    ),
    GenreFamily(
        "classic-rock", ("classic rock", "rock", "60s rock", "70s rock", "arena rock"),
        (.75, .62, .28, .55, .08, .70, .30, .88),
    ),
    GenreFamily(
        "hard-rock", ("hard rock", "heavy rock", "stoner rock"),
        (.88, .50, .15, .47, .04, .85, .43, .80),
    ),
    GenreFamily(
        "alt-indie-rock",
        ("alternative rock", "alt rock", "indie rock", "indie", "alternative", "pop rock"),
        (.72, .48, .35, .53, .10, .70, .75, .63),
    ),
    GenreFamily("grunge", ("grunge", "seattle sound"), (.78, .32, .28, .45, .10, .68, .48, .58)),
    GenreFamily(
        "prog-rock", ("progressive rock", "prog rock", "art rock", "symphonic rock"),
        (.70, .55, .36, .42, .20, .67, .37, .50),
    ),
    GenreFamily(
        "folk-rock", ("folk rock", "folk-rock", "indie folk rock"),
        (.51, .65, .62, .42, .12, .44, .45, .53),
    ),
    GenreFamily(
        "metal", ("metal", "heavy metal", "thrash metal", "death metal"),
        (.91, .35, .12, .42, .08, .83, .65, .83),
    ),
    GenreFamily(
        "nu-metal", ("nu metal", "nu-metal", "alternative metal", "rap metal"),
        (.88, .43, .10, .68, .07, .82, .81, .79),
    ),
    GenreFamily(
        "house-techno",
        ("house", "deep house", "techno", "minimal techno", "tech house", "electronic"),
        (.80, .62, .08, .90, .10, .83, .78, .83),
    ),
    GenreFamily(
        "dubstep", ("dubstep", "brostep", "future bass"),
        (.84, .40, .05, .72, .12, .77, .76, .69),
    ),
    GenreFamily(
        "idm", ("idm", "intelligent dance music", "glitch", "braindance"),
        (.55, .40, .23, .30, .48, .50, .68, .39),
    ),
    GenreFamily(
        "downtempo-trip-hop", ("downtempo", "trip-hop", "trip hop", "downtempo trip-hop", "lounge"),
        (.45, .38, .42, .45, .35, .40, .58, .52),
    ),
    GenreFamily(
        "ambient-chillout", ("ambient", "chillout", "chill out", "ambient chillout", "new age"),
        (.22, .45, .72, .18, .78, .19, .67, .40),
    ),
    GenreFamily(
        "jazz", ("jazz", "vocal jazz", "cool jazz", "smooth jazz"),
        (.45, .62, .50, .48, .45, .49, .38, .50),
    ),
    GenreFamily(
        "free-jazz", ("free jazz", "avant-garde jazz", "avant garde jazz"),
        (.58, .38, .42, .28, .70, .58, .40, .23),
    ),
    GenreFamily("bebop", ("bebop", "be-bop", "hard bop"), (.64, .68, .45, .55, .45, .69, .30, .55)),
    GenreFamily(
        "classical", ("classical", "classical music", "orchestral"),
        (.30, .55, .75, .10, .83, .20, .24, .35),
    ),
    GenreFamily(
        "baroque", ("baroque", "baroque classical", "early music"),
        (.32, .62, .75, .14, .86, .20, .18, .35),
    ),
    GenreFamily(
        "contemporary-classical", ("contemporary classical", "modern classical", "neo-classical"),
        (.28, .55, .80, .12, .82, .18, .90, .35),
    ),
    GenreFamily(
        "piano", ("piano", "solo piano", "piano music", "neoclassical piano"),
        (.32, .62, .77, .23, .70, .20, .60, .55),
    ),
    GenreFamily(
        "folk", ("folk", "traditional folk", "english folk", "indian folk"),
        (.41, .64, .72, .35, .23, .37, .48, .55),
    ),
    GenreFamily(
        "americana", ("americana", "alt-country", "roots rock"),
        (.55, .65, .56, .45, .13, .50, .42, .58),
    ),
    GenreFamily(
        "country", ("country", "country music", "country pop", "bluegrass"),
        (.62, .74, .45, .64, .07, .57, .81, .86),
    ),
    GenreFamily(
        "singer-songwriter",
        ("singer-songwriter", "singer songwriter", "songwriter", "acoustic singer-songwriter"),
        (.42, .63, .68, .40, .20, .35, .70, .84),
    ),
    GenreFamily(
        "blues", ("blues", "delta blues", "electric blues", "chicago blues"),
        (.56, .45, .55, .43, .11, .45, .35, .57),
    ),
    GenreFamily(
        "disco-dance", ("disco", "dance", "dance music", "nu-disco", "disco dance"),
        (.78, .84, .10, .92, .04, .82, .43, .86),
    ),
    GenreFamily(
        "soundtrack", ("soundtrack", "film score", "score", "movie soundtrack"),
        (.50, .52, .59, .40, .47, .49, .75, .83),
    ),
    GenreFamily(
        "world", ("world", "world music", "global music", "ethnic fusion"),
        (.56, .67, .52, .61, .22, .57, .68, .45),
    ),
    GenreFamily(
        "experimental", ("experimental", "avant-garde", "noise rock", "outsider music"),
        (.56, .38, .48, .31, .52, .52, .78, .28),
    ),
)

FAMILY_BY_ID = {family.id: family for family in FAMILIES}
