# File: test_age.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-06
"""Date of birth: the minimum age at sign-up, the one-time question for existing members, and keeping minors apart."""
import io
import json
import zipfile
from datetime import date, timedelta

import pytest

from helpers import FETCH, make_app, post_form, register, scalar, signin, signup, sql, token_of, user_id
from rockconnect import ages


def born(years, extra_days=0):
    """The birth date of someone who turns `years` today (plus/minus a number of days)."""
    return (date.fromisoformat(ages.cutoff(years)) + timedelta(days=extra_days)).isoformat()


# ------------------------------------------------------------------ the helpers
def test_parse_and_age_arithmetic():
    assert ages.parse_birth_date("1990-05-15") == date(1990, 5, 15)
    for bad in ("", None, "15/05/1990", "1990-13-01", "1990-02-30", "soon", "2999-01-01", "1700-01-01", "9999-99-99"):
        assert ages.parse_birth_date(bad) is None, bad
    assert ages.age_on(date(2000, 2, 29), date(2026, 2, 28)) == 25 and ages.age_on(date(2000, 2, 29), date(2026, 3, 1)) == 26
    assert ages.age_on(date(2008, 10, 6), date(2026, 10, 5)) == 17 and ages.age_on(date(2008, 10, 6), date(2026, 10, 6)) == 18


def test_cutoff_is_the_exact_boundary(tmp_path):
    app = make_app(tmp_path)
    with app.app_context():
        assert ages.error_for(born(18)) is None                       # turns 18 today: allowed
        assert "at least 18" in ages.error_for(born(18, 1))           # turns 18 tomorrow: not yet
        assert ages.error_for(born(60)) is None
        assert "date of birth" in ages.error_for("garbage")


# ------------------------------------------------------------------ sign-up
def test_signup_needs_a_real_date_of_birth_of_an_adult(tmp_path):
    app = make_app(tmp_path)
    client = app.test_client()
    for text, words in (("", b"date of birth"), ("not-a-date", b"date of birth"), ("2999-01-01", b"date of birth"),
                        (born(17), b"at least 18"), (born(18, 1), b"at least 18")):
        assert words in signup(client, birth_date=text).data, text
    assert scalar(app, "SELECT count(*) FROM users") == 0
    assert b"account is ready" in signup(client, birth_date=born(18)).data
    assert scalar(app, "SELECT birth_date FROM users") == born(18)


def test_the_form_asks_for_it_and_says_it_stays_private(tmp_path):
    html = make_app(tmp_path).test_client().get("/users/add").get_data(as_text=True)
    assert 'name="birth_date"' in html and 'type="date"' in html and "Never shown to anyone" in html


def test_the_date_of_birth_is_never_shown_or_sent(tmp_path):
    app = make_app(tmp_path)
    rita, sam = register(app, "rita", birth_date="1987-03-21"), register(app, "sam")
    seen = [sam.get("/users_list/%d" % user_id(app, "rita")).get_data(as_text=True), sam.get("/people").get_data(as_text=True),
            json.dumps(sam.get("/api/v1/people").get_json()), sam.get("/feed").get_data(as_text=True),
            rita.get("/edit").get_data(as_text=True), rita.get("/account/").get_data(as_text=True)]
    assert all("1987-03-21" not in page and "1987" not in page for page in seen)


def test_the_member_can_download_it_in_their_data(tmp_path):
    app = make_app(tmp_path)
    rita = register(app, "rita", birth_date="1987-03-21")
    data = json.loads(zipfile.ZipFile(io.BytesIO(rita.get("/account/export").data)).read("data.json"))
    assert data["profile"]["birth_date"] == "1987-03-21"


# ------------------------------------------------------------------ members who joined before it was asked
@pytest.fixture
def old_member(tmp_path):
    app = make_app(tmp_path)
    client = register(app, "oldtimer")
    sql(app, "UPDATE users SET birth_date = NULL")                     # joined before the date of birth existed
    return app, client


def test_a_member_without_one_is_asked_once_before_anything_else(old_member):
    app, client = old_member
    for url in ("/feed", "/gigs", "/people", "/conversations/", "/account/", "/edit"):
        r = client.get(url)
        assert r.status_code == 302 and "/account/confirm-age" in r.headers["Location"], url
    assert "next=%2Fgigs" in client.get("/gigs").headers["Location"] or "next=/gigs" in client.get("/gigs").headers["Location"]
    page = client.get("/account/confirm-age").get_data(as_text=True)
    assert "date of birth" in page and "cannot change it afterwards" in page


def test_the_gate_lets_through_what_a_member_may_always_do(old_member):
    app, client = old_member
    for url in ("/terms", "/privacy", "/cookies", "/health", "/account/export"):
        assert client.get(url).status_code == 200, url
    assert client.post("/users/signout", data={"_csrf": token_of(client, "/account/confirm-age")}).status_code == 302
    assert signin(client, "oldtimer").status_code == 200


def test_apis_answer_with_a_clear_code(old_member):
    app, client = old_member
    r = client.get("/api/v1/people")
    assert r.status_code == 403 and r.get_json()["code"] == "age_required"
    assert client.get("/conversations/unread", headers=FETCH).status_code == 403
    assert app.test_client().get("/api/v1/meta").status_code == 200       # public endpoints do not need a member


def test_giving_a_valid_date_opens_everything_and_goes_back_where_they_were(old_member):
    app, client = old_member
    r = client.post("/account/confirm-age", data={"birth_date": born(30), "next": "/gigs", "_csrf": token_of(client, "/account/confirm-age")})
    assert r.status_code == 302 and r.headers["Location"].endswith("/gigs")
    assert scalar(app, "SELECT birth_date FROM users") == born(30)
    assert client.get("/feed").status_code == 200


def test_a_bad_date_keeps_the_gate_closed(old_member):
    app, client = old_member
    r = client.post("/account/confirm-age", data={"birth_date": "yesterday-ish", "_csrf": token_of(client, "/account/confirm-age")},
                    follow_redirects=True)
    assert b"date of birth" in r.data and scalar(app, "SELECT birth_date FROM users") is None
    assert client.get("/feed").status_code == 302


def test_it_cannot_be_changed_afterwards(old_member):
    app, client = old_member
    client.post("/account/confirm-age", data={"birth_date": born(30), "_csrf": token_of(client, "/account/confirm-age")})
    client.post("/account/confirm-age", data={"birth_date": born(50), "_csrf": token_of(client, "/feed")})
    assert scalar(app, "SELECT birth_date FROM users") == born(30)
    assert client.get("/account/confirm-age").status_code == 302                 # nothing to ask any more
    edit = client.post("/edit", data={"name": "X", "email": "oldtimer@example.com", "about": "y", "kind": "fan",
                                      "birth_date": born(70), "_csrf": token_of(client, "/edit")})
    assert edit.status_code == 302 and scalar(app, "SELECT birth_date FROM users") == born(30)


def test_someone_below_the_minimum_age_is_closed_out(old_member):
    app, client = old_member
    r = client.post("/account/confirm-age", data={"birth_date": born(15), "_csrf": token_of(client, "/account/confirm-age")},
                    follow_redirects=True)
    assert b"members must be at least 18" in r.data and b"erased" in r.data
    assert scalar(app, "SELECT status FROM users") == "banned" and scalar(app, "SELECT ban_reason FROM users") == "Below the minimum age"
    assert scalar(app, "SELECT count(*) FROM sessions") == 0
    refused = signin(app.test_client(), "oldtimer")
    assert refused.status_code == 403 and b"suspended" in refused.data and b"Below the minimum age" in refused.data


def test_a_member_without_a_date_can_still_delete_their_account(old_member):
    app, client = old_member
    client.post("/account/delete", data={"password": "S3cret!pw", "confirm": "DELETE", "_csrf": token_of(client, "/account/confirm-age")})
    assert scalar(app, "SELECT count(*) FROM users") == 0


# ------------------------------------------------------------------ minors (only if the operator lowers MIN_AGE)
@pytest.fixture
def mixed(tmp_path):
    app = make_app(tmp_path, MIN_AGE=16)
    adult1, adult2 = register(app, "alex", birth_date=born(30)), register(app, "alice", birth_date=born(25))
    teen1, teen2 = register(app, "tom", birth_date=born(17)), register(app, "tina", birth_date=born(16))
    return app, adult1, adult2, teen1, teen2


def test_a_lowered_minimum_lets_a_teenager_join_but_not_a_child(tmp_path):
    app = make_app(tmp_path, MIN_AGE=16)
    assert b"account is ready" in signup(app.test_client(), birth_date=born(16)).data
    assert b"at least 16" in signup(app.test_client(), "kid", birth_date=born(15)).data


def names(client, **q):
    return sorted(p["username"] for p in client.get("/api/v1/people", query_string=q).get_json()["people"])


def test_minors_and_adults_do_not_find_each_other(mixed):
    app, alex, alice, tom, tina = mixed
    assert names(alex) == ["alex", "alice"] and names(tom) == ["tina", "tom"]
    assert ">tom<" not in alex.get("/people").get_data(as_text=True) and ">alex<" not in tom.get("/people").get_data(as_text=True)


def test_minors_and_adults_do_not_see_each_other_going(mixed):
    app, alex, alice, tom, tina = mixed
    band = register(app, "kings", kind="band", birth_date=born(40))
    when = (date.today() + timedelta(days=4)).isoformat() + "T20:00"
    band.post("/posts", data={"_csrf": token_of(band, "/feed"), "body": "gig", "event_at": when, "event_place": "Hall",
                              "event_lat": "51.5", "event_lon": "-0.1"})
    ref = str(scalar(app, "SELECT id FROM posts"))
    for member in (alex, alice, tom, tina):
        member.post("/gigs/attendance", data={"_csrf": token_of(member, "/feed"), "source": "community", "ref": ref, "status": "going"})
    who = lambda c: sorted(p["username"] for p in c.get("/api/v1/gigs/community/%s/attendees" % ref).get_json()["people"])  # noqa: E731
    assert who(alex) == ["alice"] and who(alice) == ["alex"] and who(tom) == ["tina"] and who(tina) == ["tom"]
    count = lambda c: c.get("/api/v1/gigs/community/%s/attendees" % ref).get_json()["going_count"]  # noqa: E731
    assert count(alex) == 2 and count(tom) == 2          # each side counts only its own: adults 2, minors 2 (not 4)


def test_minors_and_adults_cannot_message_each_other(mixed):
    app, alex, alice, tom, tina = mixed
    cross = alex.post("/conversations/start/%d" % user_id(app, "tom"), data={"_csrf": token_of(alex, "/feed")}, follow_redirects=True)
    assert b"cannot message this person" in cross.data
    cross = tom.post("/conversations/start/%d" % user_id(app, "alex"), data={"_csrf": token_of(tom, "/feed")}, follow_redirects=True)
    assert b"cannot message this person" in cross.data
    assert alex.post("/conversations/start/%d" % user_id(app, "alice"), data={"_csrf": token_of(alex, "/feed")}).status_code == 302
    assert tom.post("/conversations/start/%d" % user_id(app, "tina"), data={"_csrf": token_of(tom, "/feed")}).status_code == 302


def test_at_the_default_age_nothing_is_separated(tmp_path):
    app = make_app(tmp_path)
    with app.app_context():
        assert ages.side_clause({"birth_date": "1990-01-01"}) == ("", {})
        assert ages.same_side("2020-01-01", "1980-01-01") is True
    rita = register(app, "rita")
    register(app, "sam")
    assert names(rita) == ["rita", "sam"]
    post_form(rita, "/block/%d" % user_id(app, "sam"))
    assert names(rita) == ["rita"]


def test_side_clause_and_same_side_agree(tmp_path):
    app = make_app(tmp_path, MIN_AGE=16)
    with app.app_context():
        adult, teen = born(30), born(17)
        sql_adult, params = ages.side_clause({"birth_date": adult})
        assert "<=" in sql_adult and params["adult_cut"] == ages.cutoff(18)
        assert ">" in ages.side_clause({"birth_date": teen})[0]
        assert ages.same_side(adult, born(40)) and ages.same_side(teen, born(16)) and not ages.same_side(adult, teen)
        assert ages.same_side(None, adult)             # not known yet = treated as an adult
