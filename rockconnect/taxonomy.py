# File: taxonomy.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-06
"""The fixed lists members choose from: instruments, genres, and what they are looking for.

Fixed lists (not free text) so that filtering works: "bass", "bass guitar" and "bassist" would otherwise be three
different filters. The keys are stored in the database and sent by the API (the Android app shows the labels);
add to the lists freely, but never rename a key.
"""

INSTRUMENTS = {
    "guitar": "Guitar", "bass": "Bass", "drums": "Drums", "vocals": "Vocals", "keyboards": "Keyboards / piano",
    "synth": "Synth", "violin": "Violin", "cello": "Cello", "saxophone": "Saxophone", "trumpet": "Trumpet",
    "trombone": "Trombone", "flute": "Flute", "clarinet": "Clarinet", "harmonica": "Harmonica", "banjo": "Banjo",
    "mandolin": "Mandolin", "ukulele": "Ukulele", "accordion": "Accordion", "percussion": "Percussion",
    "dj": "DJ", "producer": "Producer / sound engineer", "other": "Something else",
}

GENRES = {
    "rock": "Rock", "indie_alternative": "Indie / alternative", "punk": "Punk", "metal": "Metal", "pop": "Pop",
    "electronic": "Electronic / dance", "hip_hop": "Hip-hop / rap", "rnb_soul": "R&B / soul / funk",
    "jazz": "Jazz", "blues": "Blues", "folk": "Folk", "country": "Country", "classical": "Classical",
    "reggae": "Reggae / ska", "world_latin": "World / latin", "other": "Other",
}

GOALS = {
    "gig_buddies": "Gig buddies", "jam": "Jam partners", "bandmates": "Bandmates",
    "lift": "Sharing a lift", "friends": "New friends",
}

# how well someone plays an instrument, lowest first (the order is used for "at least advanced")
LEVELS = {"beginner": "Beginner", "intermediate": "Intermediate", "advanced": "Advanced", "pro": "Professional"}
LEVEL_ORDER = list(LEVELS)

# first match wins, so the more specific words come first
_GENRE_WORDS = (
    ("punk", "punk"), ("hardcore", "punk"), ("metal", "metal"), ("indie", "indie_alternative"),
    ("alternative", "indie_alternative"), ("hip-hop", "hip_hop"), ("hip hop", "hip_hop"), ("rap", "hip_hop"),
    ("r&b", "rnb_soul"), ("soul", "rnb_soul"), ("funk", "rnb_soul"), ("electronic", "electronic"),
    ("dance", "electronic"), ("house", "electronic"), ("techno", "electronic"), ("jazz", "jazz"),
    ("blues", "blues"), ("folk", "folk"), ("singer", "folk"), ("country", "country"), ("americana", "country"),
    ("classical", "classical"), ("opera", "classical"), ("reggae", "reggae"), ("ska", "reggae"),
    ("latin", "world_latin"), ("world", "world_latin"), ("afro", "world_latin"), ("rock", "rock"), ("pop", "pop"),
)


def genre_key(text):
    """The list key for a genre as a provider writes it ('Alternative Rock', 'Hip-Hop/Rap'...), or None if unknown."""
    lowered = (text or "").strip().lower()
    if not lowered or lowered == "undefined":
        return None
    for word, key in _GENRE_WORDS:
        if word in lowered:
            return key
    return "other"


def only_valid(values, allowed):
    """The values that are real keys of `allowed`, without repeats, in the order they came."""
    seen = []
    for value in values or []:
        if value in allowed and value not in seen:
            seen.append(value)
    return seen
