<!--
  File: README.md
  Author: mrbacco04@gmail.com
  Date: 2026-10-06
-->
# rockconnect

**A community platform for bands, venues and their fans.** Members sign up as a fan, a band or a venue, share posts and
photos, announce dated gigs on a shared gig board, comment, "rock on" and chat privately. It is moderated
(reports, blocking, an admin console), privacy-ready (data export and account deletion, legal pages) and white-label:
name, tagline and colour are set from the environment, so one codebase serves many scenes.

![Landing page](docs/screenshots/landing.png)

| | |
|---|---|
| ![Feed with a gig announcement](docs/screenshots/feed.png) | ![Gig board](docs/screenshots/gigs.png) |
| ![Gigs near you](docs/screenshots/nearby.png) | ![Admin report queue](docs/screenshots/admin-reports.png) |
| ![Phone](docs/screenshots/landing-phone.png) | |

## What is in it
* **Three kinds of member:** fan, band, venue (labelled on posts, filterable in the directory), with location and website.
* **Gig board and "gigs near you":** bands and venues attach a date and place to a post; it shows on `/gigs`, soonest
  first. Members find gigs close to where they are right now (browser location, a typed town or their profile town) through
  a versioned JSON API, `GET /api/v1/gigs/nearby` ([docs/API.md](docs/API.md)). Their position is rounded, never stored and never logged.
* **Gigs from anywhere in the world:** with a Ticketmaster key (and optionally Skiddle, Songkick, PredictHQ, and bands' own Bandsintown
  pages), searching "near me" in any city fetches its concerts on first use, so a new community is never empty
  ([docs/EVENT-IMPORT.md](docs/EVENT-IMPORT.md): what each provider covers, setup, and the terms you must read first).
* **Feed:** text and photo posts, likes ("Rock on"), comments, profile walls, pagination.
* **Social: going to gigs.** Say you are going (or interested) to any gig, see who else is, filter by what they play and like,
  say hi, and find people by instrument, genre and what they look for. Plans are visible to members unless you keep them
  private; strangers reach you through message requests ([docs/SOCIAL.md](docs/SOCIAL.md)).
* **Follows, notifications, gig talk, calendar:** follow members, get a bell notice when someone you follow is going to a gig,
  chat under every gig, add gigs to Google/Apple/Outlook calendar (.ics), and tag instruments with a skill level.
* **Age check:** sign-up asks for a date of birth (never shown), minimum age 18 (`MIN_AGE`); older accounts are asked once.
* **Private chat:** live 1-to-1 conversations with an unread badge. Nobody can write to a stranger: "Message" sends a request with
  no text, and nothing can be sent by either side until the other person accepts.
* **Accounts you can trust:** bcrypt passwords with a strength policy, e-mail confirmation, forgotten-password reset,
  stay-signed-in sessions like the big social sites (30 days since your last visit, renewed on every visit, no
  countdown, "sign out on all devices"), terms/age consent recorded at sign-up.
* **Moderation:** report posts, comments, gig comments and profiles; block people; admin console with a report queue, suspend /
  unsuspend / erase, and an audit log. Suspended members are signed out at once and disappear from the site.
  **Before anyone sees it:** a review queue holds posts and comments that match a blocked-words list (curated lists for English,
  Italian, Spanish, French, German, Portuguese and Russian, broader opt-in lists for 17 more languages), that come from a brand-new
  account (first posts, links and photos), or that three established members reported. A **moderator** role can work the queues without
  being able to suspend or erase anyone ([docs/MODERATION.md](docs/MODERATION.md)).
* **Look:** light, minimal design (white cards on a pale yellow page, one accent colour, Inter Light type), responsive, white-label.
* **Abuse protection:** database-backed rate limits on sign-in, sign-up, reset mails, posting, commenting, chat, reports, exports.
* **Privacy:** members download all their data (zip) or delete their account; terms, privacy and cookie pages are
  filled in from your company details; no third-party requests at all (fonts, Bootstrap and jQuery are bundled).
* **Operations:** Alembic migrations (upgrades keep your data), health endpoint, Docker image with gunicorn,
  PostgreSQL / MySQL / SQLite, S3-compatible photo storage, strict Content-Security-Policy and security headers.

A longer, illustrated overview of everything built so far (what it does, how it was tested, what is still open) is in
[docs/rockconnect-features.docx](docs/rockconnect-features.docx).

Details: [docs/DEPLOY.md](docs/DEPLOY.md) (install, upgrade, production checklist),
[docs/MODERATION.md](docs/MODERATION.md) (running the community, privacy requests), [docs/API.md](docs/API.md),
[docs/EVENT-IMPORT.md](docs/EVENT-IMPORT.md), [docs/SOCIAL.md](docs/SOCIAL.md) and [docs/ANDROID.md](docs/ANDROID.md) (plan for the Android app).

## Try it in two minutes
```
python -m venv .venv
.venv\Scripts\activate                 # Linux/macOS: . .venv/bin/activate
pip install -r requirements-dev.txt
flask --app wsgi seed-demo             # optional: demo bands, venues, fans, gigs, photos and chats
python run.py                          # open http://localhost:3000
```
Demo accounts (password `DemoPass-2026`): `the_hollow_kings` (band), `the_basement_bar` (venue), `riff_rita` (fan).
Make yourself an admin with `flask --app wsgi make-admin <username>`. Settings go in a `.env` file next to `run.py`
(copy `.env.example`); `flask --app wsgi doctor` shows what is configured and why "gigs near you" might be empty. In development, e-mails (confirmation, reset links)
are printed in the terminal instead of being sent. The SQLite database and photos live in `instance/` and persist across
restarts; back that folder up and never delete it unless you want to erase every account.

Run the tests: `python -m pytest` (650+ tests, no network needed; the full run takes a few minutes).

## Configuration
Everything is an environment variable; [.env.example](.env.example) lists them all with comments. The ones you will
touch first:

| Variable | Purpose |
|---|---|
| `APP_ENV=production` | https-only cookies + HSTS; refuses to start with a weak `SECRET_KEY` |
| `SECRET_KEY` | cookie signing key (32+ random characters) |
| `DATABASE_URL` | `postgresql://user:pass@host/db` (default: local SQLite) |
| `SITE_NAME`, `SITE_NAME_ACCENT`, `SITE_TAGLINE`, `ACCENT_COLOR` | your brand |
| `OPERATOR_NAME`, `OPERATOR_ADDRESS`, `CONTACT_EMAIL` | printed in the legal pages |
| `SMTP_HOST` ... | real e-mail for confirmations and resets |
| `S3_BUCKET` ... | photos in S3 / R2 / B2 / MinIO instead of the local folder |
| `TICKETMASTER_API_KEY`, `SKIDDLE_API_KEY`, `SONGKICK_API_KEY`, `PREDICTHQ_API_KEY` | concerts from anywhere (each optional, off until a key is set) |
| `GEOCODER` | `nominatim` (default, OpenStreetMap) or `none`: how a typed gig place becomes map coordinates |
| `DEFAULT_WORD_LISTS` | blocked-word starter lists added once on first start (default `en,it,es,fr,de,pt,ru`; empty = none) |
| `NEW_MEMBER_HOLD_POSTS`, `NEW_MEMBER_HOLD_HOURS`, `REPORTS_AUTOHIDE` | when posts wait for a moderator: first N posts, links/photos in the first hours, N different reporters (`0` = off) |
| `TRUST_PROXY=1` | when behind nginx / Caddy / a PaaS router |
| `SESSION_MINUTES` | how long you stay signed in without visiting (default 43200 = 30 days; each visit renews it) |

## Project layout
```
run.py, wsgi.py          dev server / production entry point
rockconnect/
  __init__.py            app factory, CSRF, security headers + CSP, error pages
  settings.py            all configuration from the environment
  auth.py                sign up / in / out, e-mail confirmation, password reset, password rules
  views.py, feed.py      home, directory, profiles | posts, gigs, likes, comments
  conversations.py       private chat
  moderation.py, admin.py, modlog.py     reports, blocks | admin console (admins and moderators) | audit log
  review.py, wordlists.py, wordlists_data/   review queue and blocked-words filter | starter lists in 24 languages (open lists: CC BY 4.0)
  account.py             password change, data export, account deletion
  social.py, taxonomy.py going to gigs, who is going, people search | instrument / genre / goal / level lists
  follows.py, notifications.py, gigtalk.py, ical.py, ages.py   follows | the bell | discussion under a gig | .ics export | date of birth
  api.py, geo.py         JSON API v1 (gigs near me) | map maths + place-name lookups
  importer.py, providers.py, events.py   event import (on demand + scheduled) | Skiddle, Songkick, PredictHQ, Bandsintown | an imported event's page
  ratelimit.py, mail.py, tokens.py, storage.py, legal.py, system.py, cli.py, demo.py
  db.py, migrate.py, migrations/         schema + Alembic migrations
  templates/, static/    pages, CSS, JS, bundled Bootstrap/jQuery/font (static/vendor)
tests/                   pytest suite
Dockerfile, docker-compose.yml, docker/entrypoint.sh, .env.example, Procfile
```
Create a migration after changing `db.py`: `alembic revision --autogenerate -m "what changed"`
(a test fails if the models and the migrations disagree).

## Security notes
CSRF token on every form; output escaped; no inline scripts or styles (strict CSP); `X-Frame-Options`, `nosniff`,
`Referrer-Policy`; photos checked by file content, stored under random names and served to signed-in members only;
sessions are random tokens whose hash is stored server-side (a copied cookie stops working when the session ends);
login attempts, e-mail links and reset tokens are single-purpose, hashed and expire; private chats are visible only
to their two members; no passwords, hashes, tokens or message text in the logs (the development "console" mail backend
does print the links it would have sent).

## How sessions work
1. **Register** (`/users/add`) creates the account and sends a confirmation link.
2. **Sign in** (`/users/signin`) creates a session row in the `sessions` table with an expiry time and gives the
   browser a random token in its cookie. The database stores only a hash of the token.
3. **Every request** checks that the session still exists and has not expired, and (at most once a day) slides the
   end date forward. So, like on the big social sites, you stay signed in for weeks as long as you come back within
   `SESSION_MINUTES` (30 days by default); there is no countdown.
4. **Absent for too long, signed out, suspended, password changed, or "sign out on all devices":** the session is gone on the server, so even a copied cookie
   stops working. Page requests are redirected to the sign-in page, background requests (chat, unread badge) get a
   `401`. After signing in you return to where you were.

## License
Copyright (C) 2026 mrbacco. Dual licensed, you choose one:

- **GNU AGPL v3** for open-source use, see [LICENSE-AGPL](LICENSE-AGPL).
- **Commercial license** for closed-source or proprietary products and services, see
  [COMMERCIAL-LICENSE.md](COMMERCIAL-LICENSE.md). Contact mrbacco04@gmail.com.

Bundled third-party files (Bootstrap, jQuery, Inter) keep their own licences, see
[rockconnect/static/vendor/THIRD-PARTY.md](rockconnect/static/vendor/THIRD-PARTY.md).

## Background
rockconnect started as a Python/Flask rewrite of my NCI (National College of Ireland) Higher Diploma in Science and
Web Technologies final project, [rockonnect](https://github.com/mrbacco/rockonnect) (originally Node.js, Express,
MongoDB and Pug), and has since grown into the product described above.

Author: mrbacco (mrbacco04@gmail.com)
