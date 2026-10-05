# File: demo.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-05
"""Demo content: a believable little scene (bands, venues, fans, gigs, photos, comments, a chat).

Used by `flask seed-demo` for sales demos and screenshots. Every name is invented. The pictures are
drawn here (stage-light gradients), so there are no image files or copyright questions.
"""
import secrets
import struct
import zlib
from datetime import datetime, timedelta, timezone

from . import storage
from .auth import hash_password
from .baclog import bac_log
from .db import (commit, comments, conversations, execute, geocache, insert, likes, messages, posts,
                 users)
from .util import now_str

# (username, name, kind, location, website, about)
PEOPLE = [
    ("the_hollow_kings", "The Hollow Kings", "band", "Dublin, Ireland", "https://example.com/hollowkings",
     "Four-piece garage rock band. Loud amps, short songs, long nights. New EP out this autumn."),
    ("neon_riot", "Neon Riot", "band", "Cork, Ireland", "",
     "Synth-soaked punk from the south coast. Looking for a drummer's weekend off and a good PA."),
    ("the_basement_bar", "The Basement Bar", "venue", "Galway, Ireland", "https://example.com/basement",
     "Capacity 250. Live music six nights a week, great sound, cheap pints. Bookings open."),
    ("amp_and_anvil", "Amp & Anvil", "venue", "Dublin, Ireland", "",
     "Independent venue for heavy, loud and weird. Promoters welcome, drop us a message."),
    ("riff_rita", "Rita Moran", "fan", "Limerick, Ireland", "",
     "Gig-goer since 2009. Ask me about my ticket stub collection."),
    ("sam_sixstring", "Sam Doyle", "fan", "Dublin, Ireland", "",
     "Plays a bit, listens to a lot. Always up for a lift to a gig."),
    ("dee_drums", "Dee Kavanagh", "fan", "Cork, Ireland", "", "Drummer, mostly in the shower."),
]


# where the demo gigs and towns are on the map (so "gigs near me" works in a demo without internet)
PLACES = {
    "the basement bar, galway": (53.2744, -9.0491),
    "amp & anvil, dublin": (53.3441, -6.2675),
    "cork harbour stage": (51.8985, -8.4756),
}
TOWNS = {"dublin, ireland": (53.3498, -6.2603), "cork, ireland": (51.8985, -8.4756),
         "galway, ireland": (53.2707, -9.0568), "limerick, ireland": (52.6638, -8.6267)}


def _png(width, height, pixel):
    """A PNG from pixel(x, y) -> (r, g, b). Pure Python: no imaging library needed."""
    raw = bytearray()
    for y in range(height):
        raw.append(0)  # filter type: none
        for x in range(width):
            raw.extend(pixel(x, y))

    def chunk(kind, data):
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(raw), 6)) + chunk(b"IEND", b""))


def stage_picture(color, seed):
    """A gig-poster style picture: dark stage, a coloured spotlight cone, diagonal stripes, dust."""
    width, height = 560, 320
    cx = width * (0.3 + 0.4 * ((seed * 37) % 100) / 100)

    def pixel(x, y):
        cone = max(0.0, 1.0 - abs(x - cx) / (30 + y * 0.9))          # light widens towards the floor
        glow = cone * (0.35 + 0.65 * y / height)
        stripe = 0.035 if ((x + y) // 9) % 2 else 0.0
        grain = ((x * 7919 + y * 104729 + seed * 31) % 17) / 17 * 0.04
        base = 0.05 + stripe + grain
        return tuple(min(255, int(255 * (base + glow * c))) for c in color)

    return _png(width, height, pixel)


def _store_picture(color, seed):
    name = secrets.token_hex(16) + ".png"
    storage.get().save(name, stage_picture(color, seed))
    return name


def seed(password):
    """Create the demo scene. Returns False (and changes nothing) when it already exists."""
    if execute("SELECT 1 FROM users WHERE username = :u", u=PEOPLE[0][0]).fetchone():
        return False
    now = datetime.now(timezone.utc)
    stamp = lambda **delta: (now - timedelta(**delta)).strftime("%Y-%m-%d %H:%M:%S")  # noqa: E731
    pw_hash = hash_password(password)
    ids = {}
    for username, name, kind, location, website, about in PEOPLE:
        ids[username] = insert(
            users, username=username, name=name, email=username + "@example.com", password=pw_hash,
            about=about, kind=kind, location=location, website=website or None, email_verified=1,
            terms_accepted_at=now_str(), created_at=stamp(days=30))

    def gig(days, hour=20):
        return (now + timedelta(days=days)).replace(hour=hour, minute=0).strftime("%Y-%m-%d %H:%M")

    for town, (lat, lon) in TOWNS.items():       # known towns: typing "Galway" works without a lookup service
        insert(geocache, place=town, latitude=lat, longitude=lon, created_at=now_str())
        short = town.split(",")[0]
        insert(geocache, place=short, latitude=lat, longitude=lon, created_at=now_str())

    amber, red, teal = (1.0, 0.69, 0.0), (1.0, 0.25, 0.2), (0.2, 0.8, 0.75)
    content = [
        # (author, body, minutes ago, picture colour, event_at, event_place)
        ("the_hollow_kings", "Doors at 8, we hit the stage at 9. Bring earplugs, we are not sorry.",
         25, amber, gig(6), "The Basement Bar, Galway"),
        ("the_basement_bar", "Friday is sorted: The Hollow Kings with Neon Riot supporting. Tickets on the door.",
         95, red, gig(6), "The Basement Bar, Galway"),
        ("neon_riot", "Soundcheck vibes. New synth, new problems.", 190, teal, None, None),
        ("amp_and_anvil", "Heavy night coming up. Three bands, one very loud room.",
         60 * 7, red, gig(13, 19), "Amp & Anvil, Dublin"),
        ("riff_rita", "Last night was unreal. Who else was front row?", 60 * 20, None, None, None),
        ("the_hollow_kings", "Rehearsal room today. Setlist for the tour is locked in.",
         60 * 26, amber, None, None),
        ("sam_sixstring", "Anyone driving from Dublin to Galway on Friday? I have petrol money and snacks.",
         60 * 30, None, None, None),
        ("neon_riot", "Summer festival slot confirmed. More news soon.", 60 * 50, teal, gig(24, 17), "Cork Harbour Stage"),
    ]
    # oldest first: post ids then grow with time and the feed (newest id first) shows the newest on top
    post_ids, post_authors = {}, {}
    for n in sorted(range(len(content)), key=lambda i: -content[i][2]):
        author, body, mins_ago, colour, event_at, place = content[n]
        photo = _store_picture(colour, n + 1) if colour else None
        lat, lon = PLACES.get((place or "").lower(), (None, None))
        post_ids[n] = insert(posts, user_id=ids[author], body=body, image_filename=photo,
                             created_at=stamp(minutes=mins_ago), event_at=event_at, event_place=place,
                             latitude=lat, longitude=lon)
        post_authors[n] = ids[author]

    talk = [
        (0, "riff_rita", "Front row, ears still ringing. Worth it."),
        (0, "sam_sixstring", "Save me a spot by the barrier!"),
        (1, "dee_drums", "Neon Riot supporting? I am in."),
        (3, "riff_rita", "Will there be a bar open until late?"),
        (3, "amp_and_anvil", "Open until 2, as always."),
        (4, "sam_sixstring", "Third row, right in front of the speaker. Hearing is optional."),
    ]
    for post_index, author, body in talk:
        insert(comments, post_id=post_ids[post_index], user_id=ids[author], body=body,
               created_at=stamp(minutes=15 + post_index))
    fans = ("riff_rita", "sam_sixstring", "dee_drums", "the_basement_bar")
    for n, post_id in post_ids.items():
        for username in fans[: 1 + n % 4]:           # a different number of likes on each post
            if ids[username] != post_authors[n]:     # nobody likes their own post
                insert(likes, post_id=post_id, user_id=ids[username], created_at=stamp(minutes=10))

    low, high = sorted((ids["the_hollow_kings"], ids["the_basement_bar"]))
    chat = insert(conversations, user_low_id=low, user_high_id=high, created_at=stamp(days=2))
    for minutes_ago, sender, text in (
        (2900, "the_basement_bar", "Hi! We have a free Friday in two weeks. Interested?"),
        (2880, "the_hollow_kings", "Absolutely. What is the door split?"),
        (2870, "the_basement_bar", "70/30 in your favour, we cover the sound engineer."),
        (2860, "the_hollow_kings", "Deal. We will bring Neon Riot as support."),
    ):
        insert(messages, conversation_id=chat, sender_id=ids[sender], body=text, created_at=stamp(minutes=minutes_ago))
    commit()
    bac_log("demo", "demo scene created: %d members, %d posts" % (len(ids), len(post_ids)))
    return True
