# File: ages.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-06
"""Date of birth: checking the minimum age, and keeping minors and adults apart if the operator lowers MIN_AGE.

The date of birth is asked once (at sign-up, or the first time an existing member signs in after this feature), is only
used to check the age, and is never shown to anyone nor returned by the API. It cannot be changed afterwards by the
member (an admin can erase the account). It is self-declared: nothing here proves it is true.

With the default MIN_AGE of 18 every member is an adult and nothing below is used. If an operator sets MIN_AGE to 16
or 17, members under 18 and members of 18 or over cannot find, see or message each other (side_clause).
"""
from datetime import date

from flask import current_app

ADULT_AGE = 18
OLDEST = 120


def today():
    return date.today()


def parse_birth_date(text):
    """A date from 'YYYY-MM-DD', or None when it is not a real, past, plausible date."""
    try:
        born = date.fromisoformat((text or "").strip())
    except ValueError:
        return None
    return born if date(today().year - OLDEST, 1, 1) <= born <= today() else None


def age_on(born, on=None):
    on = on or today()
    return on.year - born.year - ((on.month, on.day) < (born.month, born.day))


def cutoff(years):
    """'YYYY-MM-DD': a member born on or before it is at least `years` old today."""
    t = today()
    try:
        return t.replace(year=t.year - years).isoformat()
    except ValueError:                      # 29 February
        return t.replace(year=t.year - years, day=28).isoformat()


def error_for(text, minimum=None):
    """Why this date of birth cannot be accepted (text for the member), or None when it is fine."""
    minimum = minimum or current_app.config["MIN_AGE"]
    born = parse_birth_date(text)
    if born is None:
        return "Enter your date of birth as day, month and year (it is never shown to anyone)."
    if age_on(born) < minimum:
        return "You must be at least %d years old to join." % minimum
    return None


def minors_apart():
    """True when this installation lets people under 18 join, so the two groups must not meet."""
    return current_app.config["MIN_AGE"] < ADULT_AGE


def side_clause(viewer, alias="u"):
    """SQL (and parameters) that keeps only members on the SAME side of 18 as `viewer`. Empty when nobody is a minor."""
    if not minors_apart():
        return "", {}
    adult_cut = cutoff(ADULT_AGE)
    viewer_born = viewer["birth_date"] if viewer is not None else None
    if viewer_born is None or viewer_born <= adult_cut:      # adult (or not yet known: treated as adult)
        return " AND COALESCE(%s.birth_date, '0000-00-00') <= :adult_cut" % alias, {"adult_cut": adult_cut}
    return " AND %s.birth_date > :adult_cut" % alias, {"adult_cut": adult_cut}


def same_side(a_born, b_born):
    """Python version of side_clause for two members (used where one pair is checked, like starting a chat)."""
    if not minors_apart():
        return True
    cut = cutoff(ADULT_AGE)
    return ((a_born is None or a_born <= cut) == (b_born is None or b_born <= cut))
