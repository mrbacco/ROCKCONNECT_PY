<!--
  File: DEPLOY.md
  Author: mrbacco04@gmail.com
  Date: 2026-10-05
-->
# Deploying rockconnect

Three ways, from simplest to most flexible. All of them are configured with environment variables only: see
[.env.example](../.env.example) for the full, commented list.

## 1. Docker Compose (app + PostgreSQL on one server)

```
cp .env.example .env            # then edit: uncomment APP_ENV, SECRET_KEY, POSTGRES_PASSWORD; set SITE_NAME, OPERATOR_*, SMTP_*
docker compose up -d --build
docker compose exec app flask --app wsgi make-admin <your-username>   # after you registered on the site
```

* The container applies database migrations first, then starts gunicorn (3 workers x 2 threads, change with
  `WEB_CONCURRENCY` / `GUNICORN_THREADS`).
* Put a reverse proxy with HTTPS in front (Caddy is the easiest: `reverse_proxy app:8000`) and set
  `TRUST_PROXY=1` and `SITE_URL=https://your.domain` in `.env`.
* Data lives in two volumes: `pgdata` (database) and `uploads` (photos). Back both up, or use S3 for photos (below).
* The image is built to run as a non-root user and has a health check on `/health`.

## 2. Any server with Python (Linux / macOS / Windows)

```
python -m venv .venv && . .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt psycopg2-binary     # psycopg2 only if you use PostgreSQL
export APP_ENV=production SECRET_KEY=<64 random hex chars> DATABASE_URL=postgresql://...
flask --app wsgi db-upgrade                         # create / update the tables
AUTO_MIGRATE=0 gunicorn wsgi:app --workers 3 --threads 2     # Windows: waitress-serve wsgi:app
```

Run it under systemd (or similar) so it restarts, and put nginx/Caddy in front for HTTPS.

## 3. Platform-as-a-service (Render, Railway, Fly, Heroku-style)

Use the included `Procfile`, set the environment variables from `.env.example` in the platform's dashboard, attach a
PostgreSQL database (`DATABASE_URL`) and an S3-compatible bucket for photos (`S3_BUCKET`...), because their
disks are usually temporary.

## Production checklist

| | |
|---|---|
| `APP_ENV=production` | turns on https-only cookies and HSTS, and **refuses to start** without a strong `SECRET_KEY` |
| `SECRET_KEY` | 32+ random characters, never reused or committed |
| HTTPS | terminate it at the proxy; set `TRUST_PROXY=1` so rate limiting sees real client addresses |
| `SESSION_MINUTES` | members stay signed in this long after their last visit (default 43200 = 30 days) |
| `CONTACT_EMAIL` | a REAL address: also sent to OpenStreetMap to identify your site (placeholders are refused) |
| `SMTP_*` | without it, no real e-mail is sent: nobody can confirm an address or reset a password. See "Email confirmation" below |
| `OPERATOR_NAME`, `OPERATOR_ADDRESS`, `CONTACT_EMAIL` | printed in the terms and privacy policy |
| Legal review | the legal pages are a starting point: have them reviewed for your country before launch |
| Backups | database + photos (or the bucket); test a restore once |
| Admin | `flask --app wsgi make-admin <username>` |

## Upgrading to a new release

```
git pull        # or unpack the new version
flask --app wsgi db-upgrade      # (docker: just `docker compose up -d --build`, it migrates on start)
```
Migrations only add things and keep existing members, posts and photos. An installation made before migrations
existed is recognised and upgraded in place; existing members count as e-mail-confirmed and as fans.

## Photos in S3-compatible storage

Set `S3_BUCKET` (+ `S3_ENDPOINT_URL` for R2 / B2 / MinIO, `S3_REGION`) and the `AWS_*` credentials. The bucket can stay
private: the app checks the sign-in and then redirects the browser to a signed link that works for 10 minutes. Needs
`boto3` (already in the Docker image, `pip install boto3` otherwise).

## Branding a copy for a customer

`SITE_NAME`, `SITE_NAME_ACCENT`, `SITE_TAGLINE`, `ACCENT_COLOR` change the name, the colour of its tail, the
tagline and the accent colour everywhere, including e-mails and the legal pages. No code change is needed. The
background picture on the landing page is `rockconnect/static/images/rockconnect.jpg`; replace the file to change it.

## Notes

* "Gigs near you" uses the browser's location feature, which only works on **https** pages (and `localhost`). Typed
  gig places are looked up with OpenStreetMap's public Nominatim by default: fine for a community, but for heavy use
  follow [their usage policy](https://operations.osmfoundation.org/policies/nominatim/), run your own Nominatim
  (`GEOCODER_URL`) or set `GEOCODER=none` (gigs then need the location button).
* The sample gunicorn/Procfile logs print request paths without query strings, on purpose: the searcher's position
  travels in the query string. If you put your own proxy in front, turn off query logging there too.

* Gig times are stored and shown exactly as the band typed them (the venue's local time, no time zone conversion).
* Chat uses short polling (every 2 s while a chat is open). That is fine for hundreds of simultaneous members; for
  thousands, move chat to WebSockets/SSE.
* SQLite is fine for a small community on one server. Use PostgreSQL for anything you sell as a service.

## Email confirmation: nobody gets an account without a valid address
With `REQUIRE_EMAIL_VERIFICATION=1` (on by itself as soon as `SMTP_HOST` is set) **signing up does not create an account**. The form is
checked, the password is hashed, and a link is mailed to the address. The account is created only when that link is opened (a button
on the page, so mail scanners that pre-open links cannot use it up). The link works once and expires after 24 hours; an unconfirmed
sign-up is then deleted. Until then nobody can sign in with it, nobody can see it, and the username is not reserved (the first
person to confirm gets it).
* Throw-away mailbox services (mailinator, guerrillamail, yopmail...) are refused at sign-up and when changing an address. Add more
  with `BLOCKED_EMAIL_DOMAINS=bad.example,worse.example`.
* "Send the link again" is rate-limited per address, and answers the same whether or not an address is waiting.
* Members who joined before this was switched on keep working; their unconfirmed address only blocks posting, commenting and messaging.

Set it up (put these in `.env`, then restart):
```
SMTP_HOST=smtp.your-provider.com
SMTP_PORT=587
SMTP_USER=your-login
SMTP_PASSWORD=your-password-or-app-password
SMTP_FROM=noreply@your-domain.com        # an address on a domain you control
SMTP_SECURITY=starttls                   # or ssl (port 465)
```
Any SMTP service works: Brevo, Mailgun, Amazon SES, Postmark, or Gmail with an app password (fine for a pilot, limited volume). For
mail to reach inboxes and not spam folders, add **SPF, DKIM and DMARC** records for the sending domain (your provider shows them).
To try the flow in development without a mail server, set `REQUIRE_EMAIL_VERIFICATION=1` and leave `SMTP_HOST` empty: the link is
printed in the terminal where the app runs.
