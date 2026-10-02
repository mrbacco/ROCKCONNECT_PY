<!--
  File: README.md
  Author: mrbacco04@gmail.com
  Date: 2026-10-02
-->
# rockconnect

A small Facebook-style social web app for people who love rock music. Visitors land on a page with only
**Sign in** and **Sign up**; once signed in, members share posts and photos, like and comment, and chat
privately with each other.

It is a Python/Flask rewrite of my NCI (National College of Ireland) Higher Diploma in Science and Web
Technologies final project, [rockonnect](https://github.com/mrbacco/rockonnect) (originally Node.js,
Express, MongoDB and Pug).

## Features
- **Accounts:** sign up, sign in and sign out, passwords hashed with bcrypt, edit your own profile.
- **Feed:** post text and/or a photo, like and comment on other members' posts, delete your own posts, and
  browse a profile "wall" for each member.
- **Chat:** private 1-to-1 conversations in a Messenger-style two-pane layout. New messages appear live
  (no page reload) and a navbar badge shows unread chats.
- **People:** search for members by username or name.

## Tech
Python 3.12, Flask, Jinja2 templates, SQLAlchemy (SQLite by default, PostgreSQL or MySQL via `DATABASE_URL`),
bcrypt, Bootstrap 4, plain JavaScript (`fetch` polling for the chat), pytest.

## Run it
```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python run.py            # open http://localhost:3000
```
The local SQLite database and uploaded photos are created in `instance/` on first run.

| Environment variable | Purpose |
|---|---|
| `SECRET_KEY` | session signing key, set it to a long random value outside local use |
| `DATABASE_URL` | remote database, e.g. `postgresql://user:pass@host:5432/db` (default: local SQLite) |
| `PORT`, `IP` | where to listen (default `0.0.0.0:3000`) |
| `FLASK_DEBUG=1` | auto-reload while developing |
| `BAC_LOG=0` | silence the `BAC_LOG` terminal logs |

## Project layout
```
run.py                  start the server
rockconnect/
  __init__.py           app factory, CSRF protection, error handlers
  auth.py               sign up / sign in / sign out
  views.py              landing page, people, profile, edit profile
  feed.py               posts, photos, likes, comments
  conversations.py      chat and unread badge
  db.py                 database tables and helpers
  baclog.py             BAC_LOG terminal logger
  templates/, static/   Jinja2 pages, CSS, JavaScript, images
tests/                  pytest suite (python -m pytest)
```

## License
Copyright (C) 2026 mrbacco. Dual licensed, you choose one:

- **GNU AGPL v3** for open-source use, see [LICENSE-AGPL](LICENSE-AGPL).
- **Commercial license** for closed-source or proprietary products and services, see
  [COMMERCIAL-LICENSE.md](COMMERCIAL-LICENSE.md). Contact mrbacco04@gmail.com.

## Security notes
CSRF token on every form, escaped output, photos checked by file content and stored under random names,
private chats visible only to their two members, and no passwords, hashes or message text in the logs.

Author: mrbacco (mrbacco04@gmail.com)
