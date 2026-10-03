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
- **Accounts and sessions:** register, then sign in, which starts a timed session (60 minutes by default).
  When it expires you must sign in again and you land back on the page you were on. A countdown in the menu bar
  shows the time left. Passwords are hashed with bcrypt, and you can edit your own profile.
- **Feed:** post text and/or a photo, like and comment on other members' posts, delete your own posts, and
  browse a profile "wall" for each member.
- **Chat:** private 1-to-1 conversations in a Messenger-style two-pane layout. New messages appear live
  (no page reload) and a navbar badge shows unread chats.
- **People:** search for members by username or name.

## Look and feel
A dark "stage" theme of its own: near-black pages, a single amber stage-light accent, Roboto Condensed type,
sharp corners with hard offset shadows (gig-poster style) and guitar-pick shaped avatars. Likes are "Rock on" and
chats are "Backstage chats". All styling is in `rockconnect/static/css/style.css`, driven by a handful of colour
variables at the top of the file.

## Tech
Python 3.12, Flask, Jinja2 templates, SQLAlchemy (SQLite by default, PostgreSQL or MySQL via `DATABASE_URL`),
bcrypt, Bootstrap 4 (restyled), Roboto Condensed (Google Fonts), plain JavaScript (`fetch` polling for the chat), pytest.

## Run it
```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python run.py            # open http://localhost:3000
```
The local SQLite database and uploaded photos are created in `instance/` on first run and **persist across
restarts**. The start-up log prints the exact data folder. `instance/` is not in git: back it up by copying the
whole folder, and never delete it unless you want to erase every account, post and photo.

| Environment variable | Purpose |
|---|---|
| `SECRET_KEY` | cookie signing key, set it to a long random value outside local use |
| `SESSION_MINUTES` | how long a login lasts before the user must sign in again (default `60`) |
| `SESSION_COOKIE_SECURE=1` | send the session cookie over https only (set this when deployed with https) |
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

## How sessions work
1. **Register** (`/users/add`) creates the account only.
2. **Sign in** (`/users/signin`) creates a session row in the `sessions` table with an expiry time and gives the
   browser a random token in its cookie. The database stores only a hash of the token.
3. **Every request** checks that the session still exists and has not expired.
4. **Expired or signed out:** the session is gone on the server, so even a copied cookie stops working. Page
   requests are redirected to the sign-in page with a message, background requests (chat, unread badge)
   get a `401` and the browser goes to sign in. After signing in you return to where you were.

Code: `rockconnect/session_store.py` and `rockconnect/auth.py`; countdown in `static/js/main.js`.
Existing logins from before this feature are invalid, so everyone signs in once more.

## Security notes
CSRF token on every form, escaped output, photos checked by file content and stored under random names,
private chats visible only to their two members, and no passwords, hashes or message text in the logs.

Author: mrbacco (mrbacco04@gmail.com)
