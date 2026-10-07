# File: test_menu.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-06
"""The main menu: icons that are always visible, with names for tooltips and screen readers."""
import re

import pytest

from helpers import make_admin, make_app, make_moderator, register, scalar, sql, token_of

ICONS = ["Feed", "Gigs", "Messages", "People", "Alerts"]


def menu(client, url="/feed"):
    html = client.get(url).get_data(as_text=True)
    nav = html[html.index("<nav"):html.index("</nav>")]
    items = re.findall(r'<a class="nav-icon([^"]*)" href="([^"]+)" title="([^"]+)"(.*?)</a>', nav, re.S)
    return html, nav, items


@pytest.fixture
def people(tmp_path):
    app = make_app(tmp_path)
    boss, mia, rita = register(app, "boss"), register(app, "mia"), register(app, "rita")
    make_admin(app, "boss")
    make_moderator(app, "mia")
    return app, boss, mia, rita


def test_a_member_sees_five_icons_with_names(people):
    app, boss, mia, rita = people
    html, nav, items = menu(rita)
    assert [title for _, _, title, _ in items] == ICONS
    for _, href, title, inner in items:
        assert "<svg" in inner and 'aria-hidden="true"' in inner                       # a drawn icon, hidden from screen readers
        assert '<span class="sr-only">%s</span>' % title in inner                      # the name is still there for them
    assert [href for _, href, _, _ in items] == ["/feed", "/gigs", "/conversations/", "/people", "/notifications"]


def test_the_menu_is_always_visible_so_there_is_no_hamburger_button(people):
    app, boss, mia, rita = people
    html, nav, items = menu(rita)
    assert "navbar-toggler" not in html and "navbar-collapse" not in html and "navbar-expand" not in html
    assert "d-none" not in "".join(cls for cls, _, _, _ in items)                      # no icon is hidden at any screen size
    assert "navbar-nav flex-row nav-icons" in nav


def test_staff_get_one_more_icon_and_members_do_not(people):
    app, boss, mia, rita = people
    assert [t for _, _, t, _ in menu(boss)[2]] == ICONS + ["Admin"]
    assert [t for _, _, t, _ in menu(mia)[2]] == ICONS + ["Moderate"]
    assert [t for _, _, t, _ in menu(rita)[2]] == ICONS
    assert menu(boss)[2][-1][1] == "/admin/" and menu(mia)[2][-1][1] == "/admin/review"


def test_the_current_page_is_highlighted_and_announced(people):
    app, boss, mia, rita = people
    for url, title in (("/feed", "Feed"), ("/gigs", "Gigs"), ("/gigs/mine", "Gigs"), ("/conversations/", "Messages"),
                       ("/people", "People"), ("/notifications", "Alerts")):
        html, nav, items = menu(rita, url)
        active = [t for cls, _, t, _ in items if "active" in cls]
        assert active == [title], (url, active)
    html, nav, items = menu(rita, "/people")
    assert 'aria-current="page"' in [inner for cls, _, t, inner in items if t == "People"][0]
    assert 'aria-current' not in [inner for cls, _, t, inner in items if t == "Feed"][0]
    assert [t for cls, _, t, _ in menu(boss, "/admin/words")[2] if "active" in cls] == ["Admin"]


def test_the_badges_are_still_on_the_icons(people):
    app, boss, mia, rita = people
    html, nav, items = menu(rita)
    messages = [inner for _, _, t, inner in items if t == "Messages"][0]
    alerts = [inner for _, _, t, inner in items if t == "Alerts"][0]
    assert 'id="unread-badge"' in messages and "d-none" in messages and 'data-url="/conversations/unread"' in messages
    assert 'id="notification-badge"' in alerts and "d-none" in alerts
    sql(app, "INSERT INTO notifications (user_id, kind, text, url, dedupe_key, created_at) VALUES (:u, 'follow', 'x', '/', 'k1', '2026-01-01 00:00:00')",
        u=scalar(app, "SELECT id FROM users WHERE username = 'rita'"))
    alerts = [inner for _, _, t, inner in menu(rita)[2] if t == "Alerts"][0]
    assert 'id="notification-badge" class="badge badge-danger "' in alerts and ">1</span>" in alerts


def test_account_and_sign_out_live_in_the_avatar_menu(people):
    app, boss, mia, rita = people
    html, nav, items = menu(rita)
    assert 'data-toggle="dropdown"' in nav and "dropdown-menu-right" in nav
    for text, href in (("My profile", "/users_list/"), ("My gigs", "/gigs/mine"), ("Account", "/account/")):
        assert re.search(r'<a class="dropdown-item" href="%s[^"]*">%s</a>' % (re.escape(href), text), nav), text
    assert re.search(r'<form method="POST" action="/users/signout">\s*<input type="hidden" name="_csrf" value="[0-9a-f]+">\s*'
                     r'<button class="dropdown-item" type="submit">Sign out</button>', nav)
    out = rita.post("/users/signout", data={"_csrf": token_of(rita, "/feed")})
    assert out.status_code == 302 and rita.get("/feed").status_code == 302


def test_search_stays_on_wide_screens_only_and_visitors_get_no_menu(people):
    app, boss, mia, rita = people
    html, nav, items = menu(rita)
    assert 'class="form-inline d-none d-lg-flex nav-search-form"' in nav and 'name="q"' in nav
    assert 'aria-label="Search"' in nav and 'aria-label="Search people"' in nav
    page = app.test_client().get("/").get_data(as_text=True)
    assert "<nav" not in page and "nav-icon" not in page


def test_the_menu_adds_no_inline_styles_or_scripts(people):
    app, boss, mia, rita = people
    html, nav, items = menu(rita)
    assert "style=" not in nav and "<script" not in nav and "onclick" not in nav          # the strict security policy stays intact


def test_the_icons_are_centred_in_the_bar_by_giving_both_sides_equal_room():
    """Logo left and search + picture right take equal room (flex 1 1 0), so the icon row sits in the exact middle."""
    import os
    css = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "rockconnect/static/css/style.css"),
               encoding="utf-8").read()
    assert ".navbar-rc .navbar-brand { flex: 1 1 0;" in css
    assert ".nav-right { display: flex; align-items: center; justify-content: flex-end; flex: 1 1 0; }" in css
    assert ".nav-icons { align-items: center; flex: 0 0 auto; margin: 0; }" in css
