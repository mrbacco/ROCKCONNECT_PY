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
| `SITE_URL` | the public address (`https://your.domain`): links in mails point here. Required with real mail |
| `SMTP_*` | without it, no real e-mail is sent: nobody can confirm an address or reset a password. See "Sending e-mail for real" below |
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

## Sending e-mail for real
Without `SMTP_HOST` nothing is sent (mails are printed in the terminal), so nobody can confirm an address or reset a password.

**1. Pick a provider.** Any SMTP service works. For a pilot, choose one with a free tier and a dashboard that shows the DNS records:
Brevo (300 mails a day free), Mailgun, Postmark, Amazon SES (cheapest at volume). Gmail with an app password works for a quick try
but sends as your Gmail address, which is fine for testing and not for members.

**2. Send from your own domain.** Add the domain in the provider's dashboard. It lists DNS records to add at your domain registrar:
* **SPF**: one `TXT` record on the domain, e.g. `v=spf1 include:<provider> ~all`. There must be only ONE `v=spf1` record: if one
  exists already, add the provider's `include:` to it.
* **DKIM**: one or two records (`TXT` or `CNAME`) with a name like `<selector>._domainkey`. The provider says when it sees them.
* **DMARC**: a `TXT` record on `_dmarc.<domain>`; start with `v=DMARC1; p=none; rua=mailto:you@<domain>` and tighten it to
  `p=quarantine` once mail is arriving fine.

DNS changes take minutes to a few hours. Gmail and Yahoo reject or spam-folder mail from domains without SPF/DKIM/DMARC.

**3. Put the settings in `.env`** (then restart):
```
SITE_URL=https://your.domain             # REQUIRED with real mail: the links in mails point here
SMTP_HOST=smtp.your-provider.com
SMTP_PORT=587
SMTP_USER=your-login
SMTP_PASSWORD=your-smtp-key              # the provider's SMTP key / app password, not your account password
SMTP_FROM="Your Site <noreply@your.domain>"
SMTP_SECURITY=starttls                   # or ssl with port 465
```
`SITE_URL` matters for security: without it the links are built from the host name a request claims, so a forged
"forgot password" request could mail a member a reset link pointing at someone else's site. The app logs a warning at startup.

**4. Check it.**
```
flask --app wsgi doctor --online         # logs in to the mail server, looks up SPF and DMARC for the SMTP_FROM domain
flask --app wsgi send-test-mail you@gmail.com
```
`send-test-mail` sends straight away and prints the server's answer (a wrong password, a sender the provider has not verified...).
Send one to a Gmail address and use "Show original": SPF, DKIM and DMARC should all say `PASS`. Then sign up on the site with a
real address and open the link.

When the site runs, a failed delivery is logged as `SMTP delivery FAILED (<the server's answer>)` and the page carries on.

To try the flow in development without a mail server, set `REQUIRE_EMAIL_VERIFICATION=1` and leave `SMTP_HOST` empty: the link is
printed in the terminal where the app runs.
