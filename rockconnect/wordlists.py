# File: wordlists.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-06
"""Starter lists for the blocked-words filter (Admin -> Blocked words -> "Add a starter list").

Two kinds, and nothing is switched on by itself except what DEFAULT_WORD_LISTS names (English and Italian):

* CURATED lists (English, Italian, Spanish, French, German, Portuguese, Russian), written for this project: common profanity,
  insults and blasphemy. Words that are also ordinary names or places are left out on purpose (the Italian "troia" is also
  the town of Troia, so only the phrases with it are listed).
* BROAD lists for more languages, from the open "List of Dirty, Naughty, Obscene, and Otherwise Bad Words" (LDNOOBW,
  https://github.com/LDNOOBW/List-of-Dirty-Naughty-Obscene-and-Otherwise-Bad-Words, licence CC BY 4.0, see wordlists_data/).
  They lean towards sexual vocabulary and are written by volunteers, so expect false matches and gaps; very short entries and
  entries that are everyday words in the curated languages are dropped when they are loaded. Read them before relying on them.

A match never deletes anything: the post waits for a moderator, who can approve it. Every entry can be removed on the admin
page. What is offensive differs by community and country, so these lists are a starting point, not a verdict.

Matching (see review.py) ignores capitals and accents, finds whole words only (for scripts written without spaces, such as
Chinese, Japanese, Korean and Thai, it finds the text anywhere), and sees through repeated letters ("porcaaa"), digits for
letters ("p0rca") and dashes or dots between the words of a phrase ("porca-troia"). Languages that add endings to words
(Turkish, Finnish, Hungarian, Polish...) match the forms that are listed, not every ending.
"""
import functools
import os
import re
import unicodedata

CURATED = {
    "it": ("Italian", [
        # bestemmie (blasphemy)
        "porco dio", "dio porco", "dio cane", "dio bestia", "dio bastardo", "dio maiale", "dio merda", "dio can",
        "porca madonna", "madonna puttana", "madonna troia", "porca troia", "porca puttana", "cristo porco",
        "porco cristo", "madonna porca",
        # parolacce e insulti
        "cazzo", "vaffanculo", "stronzo", "stronza", "puttana", "figlio di puttana", "figlio di troia", "pezzo di merda",
        "coglione", "coglioni", "minchia", "fottiti", "testa di cazzo", "succhiacazzi", "pompino", "rottinculo",
        "culattone", "frocio", "negro di merda", "zoccola",
    ]),
    "en": ("English", [
        "fuck", "fucking", "fucker", "motherfucker", "fuck you", "shit", "shithead", "bitch", "asshole", "cunt",
        "whore", "slut", "bastard", "retard", "retarded", "faggot", "fag", "nigger", "nigga", "kike", "spic", "chink",
        "tranny", "wanker", "twat", "cocksucker", "dickhead", "piss off", "kill yourself", "kys",
    ]),
    "es": ("Spanish", [
        "puta", "puto", "hijo de puta", "hijueputa", "hijoputa", "joder", "mierda", "cabron",
        "pendejo", "gilipollas", "maricon", "marica", "me cago en dios", "me cago en la virgen",
        "me cago en cristo", "me cago en la hostia", "hostia puta", "vete a la mierda", "chinga tu madre", "puto de mierda",
    ]),
    "fr": ("French", [
        "putain", "merde", "connard", "connasse", "salope", "encule", "enculer", "fils de pute", "nique ta mere",
        "ntm", "batard", "bordel de merde", "va te faire foutre", "ferme ta gueule", "ta gueule", "pute",
        "salaud", "couille", "sale negre", "sale arabe",
    ]),
    "de": ("German", [
        "scheisse", "scheiße", "arschloch", "fotze", "hurensohn", "wichser", "schlampe", "fick dich", "ficker", "nutte",
        "missgeburt", "schwuchtel", "kanake", "neger", "verpiss dich", "halt die fresse", "dreckskerl",
        "scheißkerl", "blode kuh", "fick deine mutter",
    ]),
    "pt": ("Portuguese", [
        "porra", "caralho", "foda se", "foder", "filho da puta", "puta que pariu", "puta que o pariu", "buceta",
        "vai tomar no cu", "arrombado", "viado", "babaca", "cuzao", "fdp", "vai se foder", "merda", "otario",
        "desgraçado", "desgracado",
    ]),
    "ru": ("Russian", [
        "хуй", "хуя", "хуе", "хуйня", "хуевый", "хуйло", "нахуй", "похуй", "пизда", "пиздец", "пиздеть", "пиздабол",
        "блядь", "блять", "бля", "блядина", "ебать", "ебаный", "ебанутый", "ебаться", "ебал", "ебло", "еблан", "долбоеб",
        "уебок", "уебан", "сука", "суки", "сучара", "мудак", "мудила", "залупа", "гондон", "пидор", "пидорас", "педик",
        "шлюха", "ублюдок", "мразь", "ёб твою мать", "иди нахуй", "пошел нахуй",
    ]),
}

# broad lists from LDNOOBW: code -> English name (the file is wordlists_data/<code>.txt)
BROAD = {
    "ar": "Arabic", "cs": "Czech", "da": "Danish", "fa": "Persian", "fi": "Finnish", "fil": "Filipino", "hi": "Hindi",
    "hu": "Hungarian", "ja": "Japanese", "ko": "Korean", "nl": "Dutch", "no": "Norwegian", "pl": "Polish",
    "sv": "Swedish", "th": "Thai", "tr": "Turkish", "zh": "Chinese",
}

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wordlists_data")

# entries of the broad lists that are ordinary words, names or places in a language we know (found by checking every list
# against everyday sentences, see tests/test_review.py); they would hold innocent posts, so they are not loaded
EXCLUDE = {
    "ar": set(), "cs": set(), "da": set(), "fa": set(), "fi": set(), "fil": set(), "hi": set(), "hu": set(), "ja": set(),
    "ko": set(), "nl": {"anita", "aardappels", "aardappels afgieten"}, "no": set(), "pl": set(), "sv": set(), "th": set(),
    "tr": set(), "zh": {"13.", "13点"},
}

# scripts written without spaces between words: a listed word is looked for anywhere in the text, not as a whole word
SPACELESS = re.compile("[฀-๿぀-ヿ㐀-䶿一-鿿豈-﫿가-힯ᄀ-ᇿ]")


def is_spaceless(text):
    return bool(SPACELESS.search(text or ""))


def _usable(entry):
    """Is an entry of a broad list worth loading? Very short entries in alphabets that use spaces are everyday words in some
    other language ("am" is vulgar in Turkish and the verb "to be" in English)."""
    text = "".join(c for c in unicodedata.normalize("NFKD", entry) if not unicodedata.combining(c))
    if is_spaceless(text):
        return len(text) >= 2
    letters = re.sub(r"[\W_]", "", text)
    if re.search("[a-zA-ZЀ-ӿͰ-ϿÀ-ɏ]", letters):
        return len(letters) >= 4        # Latin, Cyrillic, Greek
    return len(letters) >= 3            # Arabic, Hebrew, Devanagari...


@functools.lru_cache(maxsize=None)
def _broad_entries(code):
    path = os.path.join(DATA_DIR, code + ".txt")
    with open(path, encoding="utf-8") as handle:
        lines = [line.strip() for line in handle]
    skip = EXCLUDE.get(code, set())
    return tuple(line for line in lines if line and line not in skip and _usable(line))


def has(code):
    return code in CURATED or code in BROAD


def kind(code):
    return "curated" if code in CURATED else "broad"


def languages():
    """[(code, name, entry count, kind)] for the admin page: curated lists first, then the broad ones."""
    found = [(code, name, len(words), "curated") for code, (name, words) in CURATED.items()]
    found += [(code, name, len(_broad_entries(code)), "broad") for code, name in sorted(BROAD.items(), key=lambda kv: kv[1])]
    return found


def entries(code):
    """The raw entries of one list (empty for an unknown code)."""
    if code in CURATED:
        return list(CURATED[code][1])
    if code in BROAD:
        return list(_broad_entries(code))
    return []


def name(code):
    return CURATED[code][0] if code in CURATED else BROAD.get(code, code)
