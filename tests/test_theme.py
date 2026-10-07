# File: test_theme.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-06
"""The design is light: classes made for dark backgrounds must not come back (white text on white is invisible)."""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGE = os.path.join(ROOT, "rockconnect")
# Bootstrap classes that draw light or white text/borders: right on a dark page, invisible on ours
DARK_ONLY = re.compile(r"\b(btn-outline-light|btn-light|text-white|text-light|navbar-dark|bg-dark|badge-light|bg-black)\b")


def own_files(*folders, suffixes):
    for folder in folders:
        for base, dirs, files in os.walk(os.path.join(PACKAGE, folder)):
            dirs[:] = [d for d in dirs if d != "vendor"]          # Bootstrap's own files are not ours
            for name in files:
                if name.endswith(suffixes):
                    yield os.path.join(base, name)


def test_no_template_or_script_uses_a_class_made_for_a_dark_page():
    bad = []
    for path in own_files("templates", "static/js", suffixes=(".html", ".js")):
        for number, line in enumerate(open(path, encoding="utf-8"), 1):
            if DARK_ONLY.search(line):
                bad.append("%s:%d %s" % (os.path.relpath(path, ROOT), number, DARK_ONLY.search(line).group(0)))
    assert bad == [], bad


def test_the_buttons_the_script_draws_use_the_same_classes_as_the_page():
    """The Going / Interested buttons exist twice (the page template and gigs.js); they must look the same."""
    script = open(os.path.join(PACKAGE, "static/js/gigs.js"), encoding="utf-8").read()
    template = open(os.path.join(PACKAGE, "templates/_social_macros.html"), encoding="utf-8").read()
    for cls in ("btn-primary", "btn-outline-primary", "btn-outline-dark"):
        assert cls in script and cls in template, cls


def test_page_titles_are_centred_on_every_main_page(tmp_path):
    """Titles and the line under them are centred (cards and forms keep left-aligned text for readability)."""
    from helpers import make_admin, make_app, register
    app = make_app(tmp_path)
    boss, rita = register(app, "boss"), register(app, "rita")
    make_admin(app, "boss")
    for client, urls in ((rita, ["/gigs", "/gigs/mine", "/people", "/notifications", "/follow-requests", "/account/"]),
                         (boss, ["/admin/", "/admin/review", "/admin/reports", "/admin/words", "/admin/hidden"])):
        for url in urls:
            html = client.get(url).get_data(as_text=True)
            assert 'class="page-title"' in html, url
    for url in ("/privacy", "/terms", "/cookies"):
        assert 'card-body legal-text' in rita.get(url).get_data(as_text=True), url
    css = open(os.path.join(PACKAGE, "static/css/style.css"), encoding="utf-8").read()
    for rule in (".page-title { text-align: center", ".page-intro { text-align: center", ".legal-text h1 { text-align: center",
                 ".kind-filter { justify-content: center"):
        assert rule in css, rule
