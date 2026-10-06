# File: test_message_requests.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-06
"""Message requests: nobody can write to a stranger. Pressing Message only asks; messages start once the other accepts."""
import pytest

from helpers import FETCH, limits, make_app, post_form, register, scalar, sql, token_of, user_id


@pytest.fixture
def trio(tmp_path):
    app = make_app(tmp_path)
    return app, register(app, "ann"), register(app, "bob"), register(app, "cat")


def start(client, app, other):
    return client.post("/conversations/start/%d" % user_id(app, other), data={"_csrf": token_of(client, "/feed")})


def send(client, conv_id, body):
    return client.post("/conversations/%d" % conv_id, headers=FETCH,
                       data={"body": body, "_csrf": token_of(client, "/conversations/%d" % conv_id)})


def act(client, conv_id, what):
    return client.post("/conversations/%d/%s" % (conv_id, what),
                       data={"_csrf": token_of(client, "/conversations/%d" % conv_id)}, follow_redirects=True)


def status(app):
    return scalar(app, "SELECT status FROM conversations")


def test_pressing_message_only_sends_a_request_with_no_text(trio):
    app, ann, bob, cat = trio
    r = start(ann, app, "bob")
    assert r.status_code == 302 and "/conversations/1" in r.headers["Location"]
    assert status(app) == "pending" and scalar(app, "SELECT initiator_id FROM conversations") == user_id(app, "ann")
    assert scalar(app, "SELECT count(*) FROM messages") == 0
    page = ann.get("/conversations/1", follow_redirects=True).get_data(as_text=True)
    assert "Request sent" in page and 'id="chat-form"' not in page


def test_nobody_can_write_until_the_request_is_accepted(trio):
    app, ann, bob, cat = trio
    start(ann, app, "bob")
    sent = send(ann, 1, "hi bob, saw you at the gig")
    assert sent.status_code == 403 and "once Bob Test accepts it" in sent.get_json()["error"]
    answered = send(bob, 1, "hey ann")                                       # the asked person must accept first, too
    assert answered.status_code == 403 and "Accept the request first" in answered.get_json()["error"]
    assert scalar(app, "SELECT count(*) FROM messages") == 0 and status(app) == "pending"
    assert 'id="chat-form"' not in bob.get("/conversations/1").get_data(as_text=True)


def test_the_plain_form_gets_a_notice_not_a_message(trio):
    app, ann, bob, cat = trio
    start(ann, app, "bob")
    r = post_form(ann, "/conversations/1", page="/conversations/1", body="sneaky")
    assert b"once Bob Test accepts it" in r.data and scalar(app, "SELECT count(*) FROM messages") == 0


def test_the_recipient_sees_it_under_requests_and_in_the_badge(trio):
    app, ann, bob, cat = trio
    start(ann, app, "bob")
    assert bob.get("/conversations/unread").get_json() == {"count": 1}
    assert ann.get("/conversations/unread").get_json() == {"count": 0}      # the asker gets no badge for her own request
    inbox = bob.get("/conversations/").get_data(as_text=True)
    assert "Requests" in inbox and "ann" in inbox and "Wants to message you" in inbox
    page = bob.get("/conversations/1").get_data(as_text=True)
    assert "would like to send you messages" in page and "Accept" in page and "Decline" in page
    assert "Requests" not in cat.get("/conversations/").get_data(as_text=True)
    assert "Request sent, waiting for an answer" in ann.get("/conversations/").get_data(as_text=True)


def test_accepting_opens_the_chat_for_both(trio):
    app, ann, bob, cat = trio
    start(ann, app, "bob")
    assert b"Accepted. You can chat now." in act(bob, 1, "accept").data
    assert status(app) == "accepted" and bob.get("/conversations/unread").get_json() == {"count": 0}
    assert send(ann, 1, "great, are you going on Friday?").status_code == 201
    assert send(bob, 1, "yes!").status_code == 201
    assert "Requests" not in bob.get("/conversations/").get_data(as_text=True)


def test_declining_removes_it_for_the_recipient_and_stays_silent_for_the_sender(trio):
    app, ann, bob, cat = trio
    start(ann, app, "bob")
    assert b"Request declined" in act(bob, 1, "decline").data
    assert status(app) == "declined"
    assert bob.get("/conversations/1").status_code == 404                                # gone for Bob
    assert "ann" not in bob.get("/conversations/").get_data(as_text=True).split("Backstage chats")[1]
    assert bob.get("/conversations/unread").get_json() == {"count": 0}
    assert bob.get("/conversations/1/messages?after=0", headers=FETCH).status_code == 404
    # Ann is not told: it looks exactly like a request nobody answered yet
    page = ann.get("/conversations/1").get_data(as_text=True)
    assert "Request sent" in page and "declin" not in page.lower()
    assert send(ann, 1, "please??").status_code == 403
    assert scalar(app, "SELECT count(*) FROM messages") == 0


def test_only_the_person_asked_can_accept_or_decline(trio):
    app, ann, bob, cat = trio
    start(ann, app, "bob")
    act(ann, 1, "accept")                                                  # the asker cannot accept her own request
    assert status(app) == "pending"
    assert act(cat, 1, "accept").status_code == 404 and act(cat, 1, "decline").status_code == 404
    act(ann, 1, "decline")
    assert status(app) == "pending"
    assert app.test_client().post("/conversations/1/accept", data={}).status_code in (302, 400)


def test_the_other_person_pressing_message_lands_in_the_same_request_and_does_not_accept_it(trio):
    app, ann, bob, cat = trio
    start(ann, app, "bob")
    r = start(bob, app, "ann")                                              # Bob also clicks Message on Ann's profile
    assert "/conversations/1" in r.headers["Location"] and scalar(app, "SELECT count(*) FROM conversations") == 1
    assert status(app) == "pending"                                          # looking is not accepting
    assert "would like to send you messages" in bob.get("/conversations/1").get_data(as_text=True)
    assert scalar(app, "SELECT initiator_id FROM conversations") == user_id(app, "ann")


def test_chats_from_before_requests_stay_open(trio):
    app, ann, bob, cat = trio
    start(ann, app, "bob")
    sql(app, "UPDATE conversations SET status = 'accepted', initiator_id = NULL")
    assert [send(ann, 1, "one").status_code, send(ann, 1, "two").status_code, send(bob, 1, "three").status_code] == [201] * 3


def test_a_message_left_in_a_pending_request_from_before_is_hidden_from_the_recipient(trio):
    app, ann, bob, cat = trio
    start(ann, app, "bob")
    sql(app, "INSERT INTO messages (conversation_id, sender_id, body, created_at) VALUES (1, :a, 'old hello', '2026-01-01 00:00:00')",
        a=user_id(app, "ann"))
    assert "old hello" not in bob.get("/conversations/1").get_data(as_text=True)
    assert "old hello" not in bob.get("/conversations/").get_data(as_text=True)
    assert bob.get("/conversations/1/messages?after=0", headers=FETCH).get_json() == {"messages": []}
    assert bob.get("/conversations/unread").get_json() == {"count": 1}       # the request, not the message
    act(bob, 1, "accept")
    assert "old hello" in bob.get("/conversations/1").get_data(as_text=True)


def test_starting_new_conversations_is_limited_per_day(tmp_path):
    app = make_app(tmp_path, RATE_LIMITS=limits(chat_request_user=(2, 86400)))
    ann = register(app, "ann")
    for name in ("bob", "cat", "dee"):
        register(app, name)
    assert start(ann, app, "bob").status_code == 302 and start(ann, app, "cat").status_code == 302
    assert start(ann, app, "dee").status_code == 429                      # a third new person today
    assert start(ann, app, "bob").status_code == 302                      # going back to an existing one is fine


def test_blocking_beats_everything(trio):
    app, ann, bob, cat = trio
    start(ann, app, "bob")
    act(bob, 1, "accept")
    send(ann, 1, "hi bob")
    post_form(bob, "/block/%d" % user_id(app, "ann"))
    assert send(ann, 1, "still there?").status_code == 403
    assert start(ann, app, "cat").status_code == 302 and start(cat, app, "bob").status_code == 302
    post_form(cat, "/block/%d" % user_id(app, "ann"))
    r = ann.post("/conversations/start/%d" % user_id(app, "cat"), data={"_csrf": token_of(ann, "/feed")}, follow_redirects=True)
    assert b"cannot message this person" in r.data


def test_a_request_from_someone_you_blocked_disappears_and_cannot_be_accepted(trio):
    app, ann, bob, cat = trio
    start(ann, app, "bob")
    assert bob.get("/conversations/unread").get_json() == {"count": 1}
    post_form(bob, "/block/%d" % user_id(app, "ann"))
    assert bob.get("/conversations/unread").get_json() == {"count": 0}
    assert "Wants to message you" not in bob.get("/conversations/").get_data(as_text=True)
    assert b"cannot message this person" in act(bob, 1, "accept").data and status(app) == "pending"


def test_a_suspended_member_s_request_is_not_shown(trio):
    app, ann, bob, cat = trio
    start(ann, app, "bob")
    sql(app, "UPDATE users SET status = 'banned' WHERE username = 'ann'")
    assert bob.get("/conversations/unread").get_json() == {"count": 0}
    assert "Wants to message you" not in bob.get("/conversations/").get_data(as_text=True)


def test_the_request_screen_offers_block_and_report(trio):
    app, ann, bob, cat = trio
    start(ann, app, "bob")
    html = bob.get("/conversations/1").get_data(as_text=True)
    assert "/block/%d" % user_id(app, "ann") in html and "/report/user/%d" % user_id(app, "ann") in html
    bob.post("/block/%d" % user_id(app, "ann"), data={"_csrf": token_of(bob, "/conversations/1"), "next": "/conversations/"})
    assert scalar(app, "SELECT count(*) FROM blocks") == 1


def test_the_old_pages_still_work_for_everyone(trio):
    app, ann, bob, cat = trio
    for client in (ann, bob, cat):
        assert client.get("/conversations/").status_code == 200
    start(ann, app, "bob")
    assert bob.get("/conversations/").status_code == 200 and ann.get("/conversations/1").status_code == 200
