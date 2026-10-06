# File: test_levels.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-06
"""Instrument skill levels: saving them, showing them, and filtering by 'at least this good'."""
import pytest

from helpers import make_app, register, token_of, user_id
from rockconnect import social, taxonomy


def edit_profile(client, **extra):
    data = {"name": "X", "email": "x@example.com", "about": "hi", "kind": "fan", "_csrf": token_of(client, "/edit")}
    data.update(extra)
    return client.post("/edit", data=data, follow_redirects=True)


@pytest.fixture
def band(tmp_path):
    """Four people who play bass at different levels (or none given), one drummer."""
    app = make_app(tmp_path)
    people = {"newbie": ({"bass": "beginner"}, 1), "mid": ({"bass": "intermediate"}, 2), "ace": ({"bass": "advanced"}, 3),
              "pro": ({"bass": "pro", "drums": "intermediate"}, 4), "vague": ({"bass": ""}, 5), "drummer": ({"drums": "pro"}, 6)}
    clients = {name: register(app, name) for name in people}
    with app.app_context():
        for name, (instruments, _n) in people.items():
            social.save_tags(user_id(app, name), instruments, [], [])
    return app, clients


def names(client, **q):
    return sorted(p["username"] for p in client.get("/api/v1/people", query_string=q).get_json()["people"])


def test_levels_are_ordered_and_complete():
    assert taxonomy.LEVEL_ORDER == ["beginner", "intermediate", "advanced", "pro"]
    assert set(taxonomy.LEVELS) == set(taxonomy.LEVEL_ORDER)


def test_the_profile_form_saves_a_level_per_ticked_instrument(tmp_path):
    app = make_app(tmp_path)
    rita = register(app, "rita")
    edit_profile(rita, instruments=["guitar", "vocals"], level_guitar="advanced", level_vocals="", level_drums="pro",
                 level_cello="wizard")
    with app.app_context():
        tags = social.user_tags(user_id(app, "rita"))
    assert tags["instruments"] == ["guitar", "vocals"]
    assert tags["instrument_levels"] == {"guitar": "advanced", "vocals": None}     # drums not ticked: its level is ignored


def test_an_unknown_level_is_ignored(tmp_path):
    app = make_app(tmp_path)
    rita = register(app, "rita")
    edit_profile(rita, instruments=["bass"], level_bass="god-tier")
    with app.app_context():
        assert social.user_tags(user_id(app, "rita"))["instrument_levels"] == {"bass": None}


def test_the_form_remembers_the_levels_and_the_profile_shows_them(tmp_path):
    app = make_app(tmp_path)
    rita, sam = register(app, "rita"), register(app, "sam")
    edit_profile(rita, instruments=["bass"], level_bass="advanced")
    form = rita.get("/edit").get_data(as_text=True)
    assert 'name="level_bass"' in form and '<option value="advanced" selected>' in form
    profile = sam.get("/users_list/%d" % user_id(app, "rita")).get_data(as_text=True)
    assert "Bass &middot; Advanced" in profile
    assert "Bass" in sam.get("/people").get_data(as_text=True)


def test_at_least_this_good_includes_better_players(band):
    app, c = band
    me = c["vague"]
    assert names(me, instrument="bass", level="beginner") == ["ace", "mid", "newbie", "pro"]      # no level given: not matched
    assert names(me, instrument="bass", level="intermediate") == ["ace", "mid", "pro"]
    assert names(me, instrument="bass", level="advanced") == ["ace", "pro"]
    assert names(me, instrument="bass", level="pro") == ["pro"]
    assert names(me, instrument="bass") == ["ace", "mid", "newbie", "pro", "vague"]               # no level asked: everyone


def test_a_level_belongs_to_its_own_instrument(band):
    app, c = band
    # "pro" is professional on bass but only intermediate on drums
    assert names(c["vague"], instrument="drums", level="pro") == ["drummer"]
    assert names(c["vague"], instrument="drums", level="intermediate") == ["drummer", "pro"]


def test_a_level_alone_means_any_instrument(band):
    app, c = band
    assert names(c["vague"], level="pro") == ["drummer", "pro"]


def test_people_page_and_going_list_have_the_level_filter(band):
    app, c = band
    page = c["vague"].get("/people?instrument=bass&level=advanced").get_data(as_text=True)
    assert 'name="level"' in page and "At least advanced" in page and ">ace<" in page and ">newbie<" not in page
    assert c["vague"].get("/api/v1/lists").get_json()["levels"]["pro"] == "Professional"


def test_the_api_returns_levels_and_rejects_unknown_ones(band):
    app, c = band
    person = [p for p in c["vague"].get("/api/v1/people", query_string={"q": "ace"}).get_json()["people"] if p["username"] == "ace"][0]
    assert person["instrument_levels"] == {"bass": "advanced"}
    bad = c["vague"].get("/api/v1/people?level=wizard")
    assert bad.status_code == 400 and bad.get_json()["code"] == "bad_level"
