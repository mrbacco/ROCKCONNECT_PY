# File: gigmatch.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-06
"""Recognising the same concert listed more than once (Ticketmaster and Skiddle, a member's post and a provider).

Used to show a gig once in the search results, and so that people who said "I'm going" on different listings of the
same concert find each other. Works on dictionaries with: source, title, event_at, latitude, longitude.
"""
import re

_FILLER = {"the", "and", "live", "tour", "with", "at", "in", "of", "a", "an", "presents", "ft", "feat", "featuring",
           "tickets", "night", "show", "support", "special", "guest", "guests"}
SOURCE_RANK = {"community": 0, "ticketmaster": 1, "skiddle": 2, "songkick": 3, "predicthq": 4, "bandsintown": 5}


def words(text):
    return {w for w in re.findall(r"[^\W_]+", (text or "").lower()) if len(w) > 1 and w not in _FILLER}


def same_gig(a, b):
    """The same concert listed by two DIFFERENT sources: same day, within ~300 m, and sharing most of the name.
    Two entries of one source are distinct events (a matinee and an evening show, two bands at one festival)."""
    if a["source"] == b["source"] or a["event_at"][:10] != b["event_at"][:10]:
        return False
    if a.get("latitude") is None or b.get("latitude") is None:
        return False
    if abs(a["latitude"] - b["latitude"]) > 0.003 or abs(a["longitude"] - b["longitude"]) > 0.005:
        return False
    wa, wb = words(a["title"]), words(b["title"])
    return bool(wa and wb) and len(wa & wb) / min(len(wa), len(wb)) >= 0.5


def remove_duplicates(gigs):
    """Keep one entry per concert. Gigs announced by members come first, then the providers in a fixed order."""
    kept = []
    for gig in sorted(gigs, key=lambda x: SOURCE_RANK.get(x["source"], 9)):
        if not any(same_gig(gig, other) for other in kept):
            kept.append(gig)
    return kept
