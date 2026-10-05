# File: envfile.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-05
"""Read a `.env` file into the environment, so a local `python run.py` or `flask --app wsgi ...` picks up the same
settings as Docker does.

Rules: lines are KEY=VALUE (an `export ` prefix is fine), `#` starts a comment, quotes around a value are removed,
and a variable that is ALREADY set in the real environment is never overwritten (real settings win).
It is called by run.py and wsgi.py only, never by the app itself, so tests are not affected by a developer's file.
"""
import os

from .baclog import bac_log

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def parse(text):
    """The KEY=VALUE pairs of a .env text, in order."""
    pairs = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if not key or not key.replace("_", "").isalnum():
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()   # trailing comment on an unquoted value
        pairs.append((key, value))
    return pairs


def load(path=None):
    """Load the .env file next to the code (or `path`). Returns the names of the variables it set."""
    path = path or os.path.join(ROOT, ".env")
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8-sig") as fh:   # utf-8-sig: Windows editors like to add a BOM
        pairs = parse(fh.read())
    loaded = []
    for key, value in pairs:
        if key not in os.environ and value != "":
            os.environ[key] = value
            loaded.append(key)
    bac_log("env", "read %s: %d setting(s) applied (values are never logged)" % (os.path.basename(path), len(loaded)))
    return loaded
