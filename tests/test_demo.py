# File: test_demo.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-05
"""The demo scene (`flask seed-demo`) and the container/deploy files."""
import os
import struct
import zlib

from helpers import make_app, scalar, signin

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def seeded(tmp_path):
    app = make_app(tmp_path)
    result = app.test_cli_runner().invoke(args=["seed-demo", "--password", "Demo-Pass-123"])
    assert result.exit_code == 0, result.output
    return app, result


def test_seed_demo_builds_a_scene(tmp_path):
    app, result = seeded(tmp_path)
    assert "Demo data created" in result.output
    assert scalar(app, "SELECT count(*) FROM users WHERE kind = 'band'") == 2
    assert scalar(app, "SELECT count(*) FROM users WHERE kind = 'venue'") == 2
    assert scalar(app, "SELECT count(*) FROM users WHERE kind = 'fan'") == 3
    assert scalar(app, "SELECT count(*) FROM posts WHERE event_at IS NOT NULL") >= 3
    assert scalar(app, "SELECT count(*) FROM messages") == 4
    assert scalar(app, "SELECT min(email_verified) FROM users") == 1
    assert len(os.listdir(app.config["UPLOAD_DIR"])) >= 5


def test_seed_demo_twice_changes_nothing(tmp_path):
    app, _ = seeded(tmp_path)
    again = app.test_cli_runner().invoke(args=["seed-demo"])
    assert "already exist" in again.output
    assert scalar(app, "SELECT count(*) FROM users") == 7


def test_every_page_renders_with_demo_content(tmp_path):
    app, _ = seeded(tmp_path)
    client = app.test_client()
    assert b"Signed in as the_hollow_kings" in signin(client, "the_hollow_kings", "Demo-Pass-123").data
    for url in ("/feed", "/gigs", "/people", "/people?kind=venue", "/conversations/", "/conversations/1",
                "/users_list/1", "/users_list/3", "/account/", "/edit", "/terms", "/privacy", "/cookies"):
        assert client.get(url).status_code == 200, url
    gigs = client.get("/gigs").get_data(as_text=True)
    assert "The Basement Bar, Galway" in gigs and "Cork Harbour Stage" in gigs


def test_demo_pictures_are_valid_png_files(tmp_path):
    app, _ = seeded(tmp_path)
    for name in os.listdir(app.config["UPLOAD_DIR"]):
        data = open(os.path.join(app.config["UPLOAD_DIR"], name), "rb").read()
        assert data.startswith(b"\x89PNG\r\n\x1a\n")
        length, kind = struct.unpack(">I4s", data[8:16])
        assert kind == b"IHDR"
        width, height = struct.unpack(">II", data[16:24])
        assert (width, height) == (560, 320)
        idat = data[data.index(b"IDAT") + 4: data.index(b"IEND") - 8]
        assert len(zlib.decompress(idat)) == height * (1 + width * 3)


# ------------------------------------------------------------------ deploy files
def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


def test_deploy_files_exist_and_are_consistent():
    dockerfile = read("Dockerfile")
    entry = read("docker", "entrypoint.sh")
    assert "gunicorn" in entry and "db-upgrade" in entry
    # the access log must show the path only: "gigs near me" puts the member's position in the query string
    assert "%(U)s" in entry and "%(r)s" not in entry and "%(U)s" in read("Procfile")
    assert "USER " in dockerfile and "HEALTHCHECK" in dockerfile and "APP_ENV=production" in dockerfile
    compose = read("docker-compose.yml")
    assert "postgres" in compose and "DATABASE_URL" in compose and "SECRET_KEY" in compose
    env = read(".env.example")
    documented = {line.split("=")[0].lstrip("# ").strip() for line in env.splitlines() if "=" in line}
    for needed in ("SECRET_KEY", "SITE_NAME", "SMTP_HOST", "DATABASE_URL", "S3_BUCKET", "TRUST_PROXY", "OPERATOR_NAME"):
        assert needed in documented, needed + " missing from .env.example"
    assert "app" in read("wsgi.py")
