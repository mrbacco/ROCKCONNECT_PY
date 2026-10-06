# File: test_review.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-06
"""Moderation before publication: blocked words, new-member holds, auto-hide after reports, the moderator role."""
import io
import json
import zipfile
from datetime import datetime, timedelta

import pytest

from helpers import FETCH, make_admin, make_app, make_moderator, post_form, register, scalar, sql, token_of, user_id
from rockconnect import review, wordlists

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 32
OLD = "2020-01-01 00:00:00"


def post(client, body="hello", image=False, gig=False, place="Camden Underworld"):
    data = {"_csrf": token_of(client, "/feed"), "body": body}
    if gig:
        data.update(event_at=(datetime.now() + timedelta(days=4)).strftime("%Y-%m-%dT20:00"), event_place=place,
                    event_lat="51.539", event_lon="-0.143")
    if image:
        data["image"] = (io.BytesIO(PNG), "x.png")
    return client.post("/posts", data=data, content_type="multipart/form-data", follow_redirects=True)


def comment(client, post_id, body):
    return client.post("/posts/%d/comments" % post_id, data={"_csrf": token_of(client, "/feed"), "body": body}, follow_redirects=True)


def report(client, kind, target_id, reason="spam"):
    page = "/report/%s/%d" % (kind, target_id)
    return client.post(page, data={"_csrf": token_of(client, page), "reason": reason}, follow_redirects=True)


def add_word(admin, word):
    return post_form(admin, "/admin/words", page="/admin/words", word=word)


def see(client, url="/feed"):
    return client.get(url).get_data(as_text=True)


def state(app, table="posts", row=1):
    return scalar(app, "SELECT mod_state FROM %s WHERE id = :i" % table, i=row)


def age_accounts(app):
    sql(app, "UPDATE users SET created_at = :o", o=OLD)


@pytest.fixture
def scene(tmp_path):
    """boss = admin, mia = moderator, kings = a band, rita and sam = fans. The new-member rules are off here."""
    app = make_app(tmp_path)
    boss, mia = register(app, "boss"), register(app, "mia")
    make_admin(app, "boss")
    make_moderator(app, "mia")
    kings, rita, sam = register(app, "kings", kind="band"), register(app, "rita"), register(app, "sam")
    return app, boss, mia, kings, rita, sam


# ------------------------------------------------------------------ the pieces
def test_fold_and_word_matching_rules(scene):
    app, boss, mia, kings, rita, sam = scene
    assert review.fold("  Café   BAR ") == "cafe bar"
    for word in ("scam", "free tickets", "cafe"):
        add_word(boss, word)
    with app.app_context():
        assert review.blocked_word_in("This is a SCAM!") == "scam"
        assert review.blocked_word_in("Totally free   TICKETS here") == "free tickets"
        assert review.blocked_word_in("best Café in town") == "cafe"            # accents and capitals do not matter
        assert review.blocked_word_in("scamming is not scam") == "scam"
        assert review.blocked_word_in("scamming") is None                       # whole words only
        assert review.blocked_word_in("free of tickets") is None
        assert review.blocked_word_in("") is None


# ------------------------------------------------------------------ the blocked-words list
def test_a_post_with_a_blocked_word_is_held_and_only_its_author_sees_it(scene):
    app, boss, mia, kings, rita, sam = scene
    add_word(boss, "Scam")
    page = post(rita, "this is a SCAM, buy now")
    assert b"waiting for a quick check" in page.data and state(app) == "held"
    assert "this is a SCAM" in see(rita) and "Waiting for a quick check" in see(rita)          # the author sees it, with a notice
    for other in (sam, kings):
        assert "this is a SCAM" not in see(other)
        assert "this is a SCAM" not in see(other, "/users_list/%d" % user_id(app, "rita"))
        assert other.get("/posts/1").status_code == 404
        assert other.post("/posts/1/like", data={"_csrf": token_of(other, "/feed")}).status_code == 404
        assert comment(other, 1, "hi").status_code == 404
    assert b"Blocked word" not in page.data                                                    # the reason is for moderators


def test_the_author_is_not_told_which_word(scene):
    app, boss, mia, kings, rita, sam = scene
    add_word(boss, "zebracorn")
    page = post(rita, "a zebracorn appears").data
    assert b"waiting for a quick check" in page and b"Blocked word" not in page       # the author is not told why
    assert b"Blocked word: zebracorn" in boss.get("/admin/review").data               # moderators are


def test_staff_are_never_held(scene):
    app, boss, mia, kings, rita, sam = scene
    add_word(boss, "scam")
    post(boss, "admin says scam")
    post(mia, "moderator says scam")
    assert scalar(app, "SELECT count(*) FROM posts WHERE mod_state = 'ok'") == 2
    assert scalar(app, "SELECT count(*) FROM review_items") == 0


def test_comments_and_gig_comments_are_held_too(scene):
    app, boss, mia, kings, rita, sam = scene
    post(kings, "a normal post")
    add_word(boss, "scam")
    comment(rita, 1, "what a scam")
    assert state(app, "comments") == "held"
    assert "what a scam" in see(rita, "/posts/1") and "what a scam" not in see(sam, "/posts/1")
    assert "waiting for a check" in see(rita, "/posts/1")
    # gig discussion
    post(kings, "gig night", gig=True)
    gig_id = scalar(app, "SELECT id FROM posts WHERE event_at IS NOT NULL")
    r = rita.post("/api/v1/gigs/community/%d/comments" % gig_id, data={"_csrf": token_of(rita, "/feed"), "body": "scam tickets"})
    assert r.status_code == 201 and r.get_json()["held"] is True
    ok = sam.post("/api/v1/gigs/community/%d/comments" % gig_id, data={"_csrf": token_of(sam, "/feed"), "body": "see you there"})
    assert ok.get_json()["held"] is False
    mine = rita.get("/api/v1/gigs/community/%d/comments" % gig_id).get_json()["comments"]
    theirs = sam.get("/api/v1/gigs/community/%d/comments" % gig_id).get_json()["comments"]
    assert [(c["body"], c["pending"]) for c in mine] == [("scam tickets", True), ("see you there", False)]
    assert [c["body"] for c in theirs] == ["see you there"]
    assert "waiting for a check" in see(rita, "/posts/%d" % gig_id)


def test_a_held_gig_is_not_a_gig_until_approved(scene):
    app, boss, mia, kings, rita, sam = scene
    add_word(boss, "scam")
    post(kings, "scam night", gig=True)
    assert state(app) == "held"
    assert "scam night" not in see(sam, "/gigs")
    nearby = sam.get("/api/v1/gigs/nearby", query_string={"lat": 51.539, "lon": -0.143, "radius_km": 10}).get_json()
    assert nearby["total"] == 0
    assert sam.post("/gigs/attendance", data={"_csrf": token_of(sam, "/feed"), "source": "community", "ref": "1", "status": "going"},
                    headers=FETCH).status_code == 404
    # the band that posted it sees its own post, but it is not on the board, not in the search and cannot be joined yet
    assert "scam night" in see(kings, "/feed") and "scam night" not in see(kings, "/gigs")
    assert kings.get("/api/v1/gigs/nearby", query_string={"lat": 51.539, "lon": -0.143, "radius_km": 10}).get_json()["total"] == 0
    assert kings.post("/gigs/attendance", data={"_csrf": token_of(kings, "/feed"), "source": "community", "ref": "1", "status": "going"},
                      headers=FETCH).status_code == 404
    assert "Going" not in see(kings, "/posts/1").split("Waiting for a quick check")[1].split("</div>")[1]
    post_form(boss, "/admin/review/1/approve", page="/admin/review")
    assert "scam night" in see(sam, "/gigs") and "scam night" in see(kings, "/gigs")
    assert sam.get("/api/v1/gigs/nearby", query_string={"lat": 51.539, "lon": -0.143, "radius_km": 10}).get_json()["total"] == 1


def test_the_words_page_validates_and_is_for_admins_only(scene):
    app, boss, mia, kings, rita, sam = scene
    assert b"at least 2 letters" in add_word(boss, "x").data
    assert b"Added" in add_word(boss, "  Spam  ").data
    assert b"already on the list" in add_word(boss, "SPAM").data
    assert scalar(app, "SELECT word FROM blocked_words") == "spam"                    # stored folded
    assert b"spam" in boss.get("/admin/words").data
    assert scalar(app, "SELECT count(*) FROM mod_log WHERE action = 'add_blocked_word'") == 1
    assert b"Removed from the list" in post_form(boss, "/admin/words/1/delete", page="/admin/words").data
    assert scalar(app, "SELECT count(*) FROM blocked_words") == 0
    for client in (mia, rita):
        assert client.get("/admin/words").status_code == 404
        assert client.post("/admin/words", data={"_csrf": token_of(client, "/feed"), "word": "x y"}).status_code == 404
        assert client.post("/admin/words/1/delete", data={"_csrf": token_of(client, "/feed")}).status_code == 404


def test_the_list_has_a_size_limit(scene, monkeypatch):
    app, boss, mia, kings, rita, sam = scene
    monkeypatch.setattr(review, "MAX_WORDS", 2)
    add_word(boss, "aaa"), add_word(boss, "bbb")
    assert b"The list is full" in add_word(boss, "ccc").data and scalar(app, "SELECT count(*) FROM blocked_words") == 2


# ------------------------------------------------------------------ the review queue
def test_approving_shows_the_post_to_everyone(scene):
    app, boss, mia, kings, rita, sam = scene
    add_word(boss, "scam")
    post(rita, "legit scam awareness post")
    assert b"legit scam awareness post" in mia.get("/admin/review").data
    assert b"Approved" in post_form(mia, "/admin/review/1/approve", page="/admin/review").data
    assert state(app) == "ok" and "legit scam awareness post" in see(sam)
    assert scalar(app, "SELECT resolution FROM review_items") == "approved"
    assert scalar(app, "SELECT count(*) FROM mod_log WHERE action = 'approve_post'") == 1
    assert b"already dealt with" in post_form(boss, "/admin/review/1/approve", page="/admin/review").data


def test_removing_deletes_the_post_and_its_photo(scene):
    app, boss, mia, kings, rita, sam = scene
    add_word(boss, "scam")
    post(rita, "scam with a photo", image=True)
    assert len(list(__import__("os").listdir(app.config["UPLOAD_DIR"]))) == 1
    post_form(mia, "/admin/review/1/remove", page="/admin/review")
    assert scalar(app, "SELECT count(*) FROM posts") == 0 and scalar(app, "SELECT resolution FROM review_items") == "removed"
    assert __import__("os").listdir(app.config["UPLOAD_DIR"]) == []
    assert "scam with a photo" not in see(rita)


def test_remove_and_suspend_is_for_admins(scene):
    app, boss, mia, kings, rita, sam = scene
    add_word(boss, "scam")
    post(rita, "scam one")
    post(sam, "scam two")
    assert mia.post("/admin/review/1/remove", data={"_csrf": token_of(mia, "/admin/review"), "also_suspend": "1"}).status_code == 403
    assert scalar(app, "SELECT count(*) FROM posts") == 2 and scalar(app, "SELECT status FROM users WHERE username = 'rita'") == "active"
    assert b"admin" in boss.get("/admin/review").data
    post_form(boss, "/admin/review/1/remove", page="/admin/review", also_suspend="1")
    assert scalar(app, "SELECT status FROM users WHERE username = 'rita'") == "banned"
    assert scalar(app, "SELECT resolution FROM review_items WHERE id = 1") == "removed_banned"
    assert 'name="also_suspend"' not in mia.get("/admin/review").get_data(as_text=True)


def test_items_whose_content_was_deleted_by_the_author_vanish_from_the_queue(scene):
    app, boss, mia, kings, rita, sam = scene
    add_word(boss, "scam")
    post(rita, "scam post")
    post_form(rita, "/posts/1/delete", page="/feed")
    assert b"scam post" not in mia.get("/admin/review").data
    assert scalar(app, "SELECT status FROM review_items") == "closed" and scalar(app, "SELECT resolution FROM review_items") == "gone"


def test_only_staff_open_the_queue_and_the_menu_shows_how_many_wait(scene):
    app, boss, mia, kings, rita, sam = scene
    for client in (rita, sam, kings):
        assert client.get("/admin/review").status_code == 404
        assert client.post("/admin/review/1/approve", data={"_csrf": token_of(client, "/feed")}).status_code == 404
    assert app.test_client().get("/admin/review").status_code == 302
    assert 'id="review-badge"' not in see(boss)
    add_word(boss, "scam")
    post(rita, "scam")
    assert '<span class="badge badge-danger" id="review-badge">1</span>' in see(boss)
    assert 'id="review-badge"' in see(mia) and "Moderate" in see(mia)
    assert 'id="review-badge"' not in see(rita)


# ------------------------------------------------------------------ the moderator role
def test_a_moderator_can_work_the_queues_but_nothing_else(scene):
    app, boss, mia, kings, rita, sam = scene
    assert mia.get("/admin/review").status_code == 200 and mia.get("/admin/reports").status_code == 200
    for url in ("/admin/", "/admin/users", "/admin/words", "/admin/hidden"):
        assert mia.get(url).status_code == 404, url
    assert mia.post("/admin/users/%d/ban" % user_id(app, "rita"), data={"_csrf": token_of(mia, "/admin/review")}).status_code == 404
    page = mia.get("/admin/review").get_data(as_text=True)
    assert "Review" in page and "Reports" in page and "Members" not in page and "Blocked words" not in page


def test_a_moderator_resolves_reports_but_cannot_suspend(scene):
    app, boss, mia, kings, rita, sam = scene
    post(rita, "a post nobody likes")
    report(sam, "post", 1)
    assert mia.post("/admin/reports/1/resolve", data={"_csrf": token_of(mia, "/admin/reports"), "action": "remove_ban"}).status_code == 403
    assert scalar(app, "SELECT status FROM users WHERE username = 'rita'") == "active"
    assert 'value="remove_ban"' not in mia.get("/admin/reports").get_data(as_text=True)          # no button for moderators
    assert 'value="remove_ban"' in boss.get("/admin/reports").get_data(as_text=True)
    post_form(mia, "/admin/reports/1/resolve", page="/admin/reports", action="remove")
    assert scalar(app, "SELECT count(*) FROM posts") == 0


# ------------------------------------------------------------------ the new-member rules
@pytest.fixture
def strict(tmp_path):
    app = make_app(tmp_path, NEW_MEMBER_HOLD_POSTS=2, NEW_MEMBER_HOLD_HOURS=24)
    boss = register(app, "boss")
    make_admin(app, "boss")
    return app, boss, register(app, "rita"), register(app, "sam")


def test_the_first_posts_wait_until_enough_were_approved(strict):
    app, boss, rita, sam = strict
    post(rita, "first")
    post(rita, "second")
    assert [state(app, row=1), state(app, row=2)] == ["held", "held"]
    assert scalar(app, "SELECT reason FROM review_items WHERE id = 1").startswith("New member: post 1 of the first 2")
    post_form(boss, "/admin/review/1/approve", page="/admin/review")
    post(rita, "third")
    assert state(app, row=3) == "held"                         # one approved, two needed
    post_form(boss, "/admin/review/2/approve", page="/admin/review")
    post(rita, "fourth")
    assert state(app, row=4) == "ok" and "fourth" in see(sam)  # trusted from now on
    assert "first" in see(sam) and "third" not in see(sam)


def test_a_link_or_photo_from_a_brand_new_account_waits_and_an_old_account_is_free(strict):
    app, boss, rita, sam = strict
    sql(app, "UPDATE users SET created_at = :o WHERE username = 'rita'", o=OLD)
    sql(app, "UPDATE users SET created_at = :o WHERE username = 'boss'", o=OLD)
    post_form(boss, "/admin/review/999/approve", page="/feed")
    post(rita, "plain 1"), post(rita, "plain 2")
    assert [state(app, row=1), state(app, row=2)] == ["held", "held"]             # first-posts rule still applies
    sql(app, "UPDATE posts SET mod_state = 'ok'")
    post(rita, "visit https://example.com now")
    post(rita, "photo", image=True)
    assert [state(app, row=3), state(app, row=4)] == ["ok", "ok"]                  # an old account may post links and photos
    post(sam, "check www.example.com")
    assert state(app, row=5) == "held" and scalar(app, "SELECT reason FROM review_items WHERE target_id = 5").startswith("New member (under 24h): link")
    post(sam, "my photo", image=True)
    assert scalar(app, "SELECT reason FROM review_items WHERE target_id = 6").endswith("photo")


def test_comments_with_links_from_new_accounts_wait_but_plain_comments_do_not(strict):
    app, boss, rita, sam = strict
    post(boss, "welcome")
    comment(sam, 1, "nice one")
    comment(sam, 1, "see http://spam.example")
    assert [state(app, "comments", 1), state(app, "comments", 2)] == ["ok", "held"]


def test_the_new_member_rules_can_be_switched_off(tmp_path):
    app = make_app(tmp_path, NEW_MEMBER_HOLD_POSTS=0, NEW_MEMBER_HOLD_HOURS=0)
    rita = register(app, "rita")
    post(rita, "https://example.com first", image=True)
    assert state(app) == "ok"


# ------------------------------------------------------------------ reports hide content
@pytest.fixture
def crowd(tmp_path):
    """Three established reporters (old accounts) and one brand-new one."""
    app = make_app(tmp_path)
    boss = register(app, "boss")
    make_admin(app, "boss")
    author = register(app, "author", kind="band")
    reporters = [register(app, "rep%d" % n) for n in range(1, 4)]
    newbie = register(app, "newbie")
    sql(app, "UPDATE users SET created_at = :o WHERE username <> 'newbie'", o=OLD)
    return app, boss, author, reporters, newbie


def test_three_reports_hide_a_post_until_a_moderator_looks(crowd):
    app, boss, author, (r1, r2, r3), newbie = crowd
    post(author, "contested post")
    report(r1, "post", 1), report(r2, "post", 1)
    assert state(app) == "ok"
    report(r3, "post", 1)
    assert state(app) == "hidden"
    assert "contested post" not in see(newbie) and newbie.get("/posts/1").status_code == 404
    assert "reported and is hidden" in see(author)                                     # the author is told
    assert scalar(app, "SELECT reason FROM review_items") == "Reported by 3 members"
    assert b"contested post" in boss.get("/admin/review").data


def test_reports_from_brand_new_accounts_and_the_same_person_do_not_count(crowd):
    app, boss, author, (r1, r2, r3), newbie = crowd
    post(author, "contested post")
    report(r1, "post", 1), report(r1, "post", 1), report(r2, "post", 1)       # r1 twice is still one reporter
    report(newbie, "post", 1)                                                   # created a minute ago: does not count
    assert state(app) == "ok"
    report(r3, "post", 1)
    assert state(app) == "hidden"


def test_comments_and_gig_comments_hide_after_reports_too(crowd):
    app, boss, author, (r1, r2, r3), newbie = crowd
    post(author, "a post", gig=True)
    comment(author, 1, "zippy remark")
    for r in (r1, r2, r3):
        report(r, "comment", 1)
    assert state(app, "comments") == "hidden" and "zippy remark" not in see(newbie, "/posts/1")
    gig = author.post("/api/v1/gigs/community/1/comments", data={"_csrf": token_of(author, "/feed"), "body": "gig talk"})
    cid = gig.get_json()["id"]
    for r in (r1, r2, r3):
        report(r, "gigcomment", cid)
    assert state(app, "gig_comments", cid) == "hidden"
    assert [c["body"] for c in newbie.get("/api/v1/gigs/community/1/comments").get_json()["comments"]] == []


def test_dismissing_the_reports_brings_hidden_content_back(crowd):
    app, boss, author, (r1, r2, r3), newbie = crowd
    post(author, "contested post")
    for r in (r1, r2, r3):
        report(r, "post", 1)
    assert state(app) == "hidden"
    post_form(boss, "/admin/reports/1/resolve", page="/admin/reports", action="dismiss")
    assert state(app) == "ok" and "contested post" in see(newbie)
    assert scalar(app, "SELECT resolution FROM review_items") == "approved"


def test_approving_in_the_review_queue_closes_the_reports(crowd):
    app, boss, author, (r1, r2, r3), newbie = crowd
    post(author, "contested post")
    for r in (r1, r2, r3):
        report(r, "post", 1)
    post_form(boss, "/admin/review/1/approve", page="/admin/review")
    assert state(app) == "ok" and scalar(app, "SELECT count(*) FROM reports WHERE status = 'open'") == 0
    assert scalar(app, "SELECT resolution FROM reports LIMIT 1") == "dismissed"
    report(newbie, "post", 1)
    assert state(app) == "ok"                                                   # the old reports are settled, one new one is not enough


def test_removing_through_the_reports_closes_the_review_item(crowd):
    app, boss, author, (r1, r2, r3), newbie = crowd
    post(author, "contested post")
    for r in (r1, r2, r3):
        report(r, "post", 1)
    post_form(boss, "/admin/reports/1/resolve", page="/admin/reports", action="remove")
    assert scalar(app, "SELECT count(*) FROM posts") == 0
    assert scalar(app, "SELECT status FROM review_items") == "closed"


def test_auto_hide_can_be_switched_off(tmp_path):
    app = make_app(tmp_path, REPORTS_AUTOHIDE=0)
    author = register(app, "author")
    others = [register(app, "voter%d" % n) for n in range(4)]
    post(author, "contested")
    sql(app, "UPDATE users SET created_at = :o", o=OLD)
    for r in others:
        report(r, "post", 1)
    assert state(app) == "ok"


# ------------------------------------------------------------------ privacy and housekeeping
def test_erasing_a_member_removes_their_review_items_and_the_text_kept_in_them(scene):
    app, boss, mia, kings, rita, sam = scene
    add_word(boss, "scam")
    post(rita, "my secret scam words")
    assert scalar(app, "SELECT count(*) FROM review_items") == 1
    rita.post("/account/delete", data={"_csrf": token_of(rita, "/account/"), "password": "S3cret!pw", "confirm": "DELETE"})
    assert scalar(app, "SELECT count(*) FROM review_items") == 0 and scalar(app, "SELECT count(*) FROM posts") == 0


def test_existing_content_keeps_working_and_the_export_still_runs(scene):
    app, boss, mia, kings, rita, sam = scene
    post(rita, "ordinary")
    data = json.loads(zipfile.ZipFile(io.BytesIO(rita.get("/account/export").data)).read("data.json"))
    assert [p["body"] for p in data["posts"]] == ["ordinary"] and "ordinary" in see(sam)


def test_the_cli_makes_and_removes_moderators(scene):
    app, boss, mia, kings, rita, sam = scene
    runner = app.test_cli_runner()
    assert "now moderator" in runner.invoke(args=["make-moderator", "rita"]).output
    assert scalar(app, "SELECT role FROM users WHERE username = 'rita'") == "moderator"
    assert "now member" in runner.invoke(args=["remove-moderator", "rita"]).output
    assert scalar(app, "SELECT role FROM users WHERE username = 'rita'") == "member"
    assert runner.invoke(args=["make-moderator", "nobody"]).exit_code != 0


# ------------------------------------------------------------------ other languages and spelling tricks
def test_the_filter_works_in_any_language_including_blasphemy_with_tricks(scene):
    app, boss, mia, kings, rita, sam = scene
    for phrase in ("porca troia", "дурак", "ΜΑΛΑΚΑΣ"):          # Italian, Russian, Greek: the filter only compares text
        add_word(boss, phrase)
    with app.app_context():
        for text in ("Porca Troia!", "PORCA   TROIA", "porca-troia", "porca. troia", "porcaaa troiaaa", "p0rca tr0ia",
                     "porca_troia", "che cosa dici, porca troia?", "ПУСТЬ ДУРАК", "μαλακασ", "Μαλακας"):
            assert review.blocked_word_in(text), text
        for text in ("porca", "troia", "la città di Troia", "porcari e troie", "porcatroia", "tr oia", "дуракам", "hello"):
            assert review.blocked_word_in(text) is None, text


def test_repeated_letters_do_not_make_innocent_words_match(scene):
    app, boss, mia, kings, rita, sam = scene
    add_word(boss, "ass")
    add_word(boss, "cazzo")
    with app.app_context():
        for text in ("what an ass", "assss", "Ass.", "a$$ is not it", "c4zzo", "cazzooo", "cazzo!"):
            assert review.blocked_word_in(text), text
        for text in ("class", "as so", "I was so sure", "assistant", "pass", "bass", "cazzola", "Massa", "scazzo"):
            assert review.blocked_word_in(text) is None, text


def test_starter_lists_are_added_with_one_click_and_can_be_edited(scene):
    app, boss, mia, kings, rita, sam = scene
    assert scalar(app, "SELECT count(*) FROM blocked_words") == 0
    page = boss.get("/admin/words").get_data(as_text=True)
    for code, name, n, kind in wordlists.languages():
        assert name in page and n >= 10, code
    assert b"Added" in post_form(boss, "/admin/words/starter", page="/admin/words", lang="it").data
    assert scalar(app, "SELECT count(*) FROM blocked_words WHERE word = 'porca troia'") == 1
    assert scalar(app, "SELECT count(*) FROM blocked_words WHERE word = 'troia'") == 0          # the town of Troia is not a swear word
    assert scalar(app, "SELECT count(*) FROM mod_log WHERE action = 'add_word_list'") == 1
    again = post_form(boss, "/admin/words/starter", page="/admin/words", lang="it")
    assert b"Nothing new to add" in again.data
    n_before = scalar(app, "SELECT count(*) FROM blocked_words")
    post_form(boss, "/admin/words/starter", page="/admin/words", lang="en")
    assert scalar(app, "SELECT count(*) FROM blocked_words") > n_before
    # the admin removes an entry that does not suit the community; it stays gone until the list is added again
    post_form(boss, "/admin/words/%d/delete" % scalar(app, "SELECT id FROM blocked_words WHERE word = 'cazzo'"), page="/admin/words")
    assert scalar(app, "SELECT count(*) FROM blocked_words WHERE word = 'cazzo'") == 0
    # members are held by the starter list
    post(rita, "Che bello, porca troia!")
    assert state(app) == "held" and scalar(app, "SELECT reason FROM review_items") == "Blocked word: porca troia"
    post(rita, "Gita a Troia, in Puglia")
    assert state(app, row=2) == "ok"


def test_every_starter_entry_is_sane(scene):
    for code, (name, entries) in wordlists.CURATED.items():
        cleaned = [review.clean_word(e) for e in entries]
        assert all(len(c) >= 2 for c in cleaned), code
        assert len(set(cleaned)) >= len(cleaned) - 3, code                 # almost no duplicates
        for c in cleaned:
            assert c == review.fold(c) and "  " not in c, (code, c)
    assert wordlists.entries("xx") == []


def test_starter_lists_are_for_admins_only_and_validated(scene):
    app, boss, mia, kings, rita, sam = scene
    for client in (mia, rita):
        assert client.post("/admin/words/starter", data={"_csrf": token_of(client, "/feed"), "lang": "it"}).status_code == 404
    assert boss.post("/admin/words/starter", data={"_csrf": token_of(boss, "/admin/words"), "lang": "klingon"}).status_code == 400
    assert boss.post("/admin/words/starter", data={"_csrf": token_of(boss, "/admin/words")}).status_code == 400
    assert scalar(app, "SELECT count(*) FROM blocked_words") == 0


def test_a_full_list_stops_the_starter_list(scene, monkeypatch):
    app, boss, mia, kings, rita, sam = scene
    monkeypatch.setattr(review, "MAX_WORDS", 5)
    post_form(boss, "/admin/words/starter", page="/admin/words", lang="it")
    assert scalar(app, "SELECT count(*) FROM blocked_words") == 5


# ------------------------------------------------------------------ out of the box
def test_a_new_site_filters_swearing_without_anyone_touching_the_admin_page(tmp_path):
    app = make_app(tmp_path, DEFAULT_WORD_LISTS="en,it")
    assert scalar(app, "SELECT count(*) FROM blocked_words WHERE word = 'porca troia'") == 1
    assert scalar(app, "SELECT count(*) FROM blocked_words WHERE word = 'fuck'") == 1
    assert scalar(app, "SELECT count(*) FROM blocked_words WHERE word = 'puta'") == 0              # Spanish was not asked for
    assert scalar(app, "SELECT target FROM mod_log WHERE action = 'seed_word_lists'") == "en,it"
    kings, rita, sam = register(app, "kings", kind="band"), register(app, "rita"), register(app, "sam")
    post(kings, "a lovely night")
    comment(rita, 1, "porca troia che concerto")
    comment(rita, 1, "what the FUCK, great gig")
    comment(rita, 1, "great gig")
    assert [state(app, "comments", n) for n in (1, 2, 3)] == ["held", "held", "ok"]
    page = see(sam, "/posts/1")
    assert "great gig" in page and "che concerto" not in page and "FUCK" not in page


def test_the_starter_lists_are_added_once_and_removed_words_stay_removed(tmp_path):
    app = make_app(tmp_path, DEFAULT_WORD_LISTS="it")
    boss = register(app, "boss")
    make_admin(app, "boss")
    first = scalar(app, "SELECT count(*) FROM blocked_words")
    assert first > 15
    sql(app, "DELETE FROM blocked_words")                                      # an admin clears the list on purpose
    again = make_app(tmp_path, DEFAULT_WORD_LISTS="it")                        # the site restarts on the same database
    assert scalar(again, "SELECT count(*) FROM blocked_words") == 0
    assert scalar(again, "SELECT count(*) FROM mod_log WHERE action = 'seed_word_lists'") == 1
    assert b"nothing is being filtered" in boss.get("/admin/words").data      # and the admin page says so


def test_default_lists_can_be_switched_off_and_bad_codes_are_ignored(tmp_path):
    assert scalar(make_app(tmp_path / "a", DEFAULT_WORD_LISTS=""), "SELECT count(*) FROM blocked_words") == 0
    other = make_app(tmp_path / "b", DEFAULT_WORD_LISTS="klingon, ,it")
    assert scalar(other, "SELECT target FROM mod_log WHERE action = 'seed_word_lists'") == "it"
    assert scalar(make_app(tmp_path / "c", DEFAULT_WORD_LISTS="klingon"), "SELECT count(*) FROM mod_log WHERE action = 'seed_word_lists'") == 0


def test_the_words_page_warns_when_the_list_is_empty_and_that_admins_are_never_held(scene):
    app, boss, mia, kings, rita, sam = scene
    page = boss.get("/admin/words").get_data(as_text=True)
    assert "nothing is being filtered" in page and "never held" in page
    add_word(boss, "spam")
    assert "nothing is being filtered" not in boss.get("/admin/words").get_data(as_text=True)
