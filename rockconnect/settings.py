# File: settings.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-05
"""All configuration in one place: environment variables -> a plain dict of Flask config values.

A customer re-brands and configures the whole product from the environment (see .env.example and
docs/DEPLOY.md): no code changes are needed. Tests pass their own dict on top of these defaults.
"""
import os
import re

DEFAULT_SECRET = "dev-only-change-me"
DEFAULT_ACCENT = "#ffc400"

# name -> (how many events, in how many seconds). Used by ratelimit.py; override in test_config
# or with RATE_LIMIT_<NAME>="count/seconds" in the environment (e.g. RATE_LIMIT_SIGNIN_IP=50/900).
DEFAULT_RATE_LIMITS = {
    "signin_ip":     (30, 900),    # failed sign-ins per IP address
    "signin_user":   (10, 900),    # failed sign-ins per username (stops password guessing)
    "signup_ip":     (10, 3600),   # new accounts per IP address
    "forgot_ip":     (10, 3600),   # password reset mails per IP address
    "forgot_email":  (3, 3600),    # password reset mails per mailbox
    "verify_user":   (5, 3600),    # "send the verification mail again"
    "post_user":     (20, 600),    # posts per member
    "comment_user":  (60, 600),    # comments per member
    "message_user":  (120, 300),   # chat messages per member
    "report_user":   (10, 3600),   # reports per member
    "export_user":   (5, 3600),    # data exports per member
    "password_user": (10, 3600),   # password change / account delete attempts per member
    "nearby_user":   (60, 600),    # "gigs near me" searches per member
    "geocode_user":  (20, 3600),   # place-name lookups per member (they go to the geocoding service)
    "ondemand_site": (60, 3600),   # fetches of new areas from the event providers, for the whole site
    "artist_sync_user": (6, 3600), # "refresh my Bandsintown dates" per band
    "attend_user":   (60, 3600),   # going / interested clicks per member
    "follow_user":   (60, 3600),   # follow / unfollow clicks per member
    "gigtalk_user":  (30, 3600),   # comments under gigs per member
    "people_user":   (120, 600),   # people searches and attendee lists per member
    "chat_request_user": (15, 86400),  # new conversations (message requests) started per member and day
}


def env_bool(name, default=False):
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _rate_limits():
    limits = dict(DEFAULT_RATE_LIMITS)
    for name in limits:
        raw = os.environ.get("RATE_LIMIT_" + name.upper())
        if raw and re.fullmatch(r"\d+/\d+", raw):
            count, seconds = raw.split("/")
            limits[name] = (int(count), int(seconds))
    return limits


def accent_color(value):
    """Only accept #rrggbb (it is written into CSS), otherwise fall back to the default amber."""
    return value if value and re.fullmatch(r"#[0-9a-fA-F]{6}", value) else DEFAULT_ACCENT


def build():
    """Read the environment and return the default configuration."""
    env = os.environ.get
    production = env("APP_ENV", "development").lower() == "production"
    smtp_host = env("SMTP_HOST", "")
    # like the big social sites: you stay signed in until you sign out, as long as you come back at least once
    # in this time (30 days by default); every visit pushes the end date forward (see session_store.renew)
    session_minutes = int(env("SESSION_MINUTES", 60 * 24 * 30))
    return {
        "APP_ENV": "production" if production else "development",
        "SECRET_KEY": env("SECRET_KEY", DEFAULT_SECRET),
        "SESSION_LIFETIME_MINUTES": session_minutes,
        "SESSION_RENEW_MINUTES": int(env("SESSION_RENEW_MINUTES", 60 * 24)),  # extend at most once a day
        "SESSION_COOKIE_HTTPONLY": True,
        "SESSION_COOKIE_SAMESITE": "Lax",
        # production = https only, unless explicitly switched off (e.g. behind a plain-http test proxy)
        "SESSION_COOKIE_SECURE": env_bool("SESSION_COOKIE_SECURE", production),
        "HSTS": env_bool("HSTS", production),
        "TRUST_PROXY": env_bool("TRUST_PROXY", False),  # behind nginx/Caddy/a PaaS router: use X-Forwarded-*
        "SEND_FILE_MAX_AGE_DEFAULT": 3600,
        "MAX_CONTENT_LENGTH": 6 * 1024 * 1024,
        "AUTO_MIGRATE": env_bool("AUTO_MIGRATE", True),

        # ---- branding: every customer can re-skin the product from the environment ----
        "SITE_NAME": env("SITE_NAME", "rockconnect"),
        "SITE_NAME_ACCENT": env("SITE_NAME_ACCENT", "connect"),  # the tail of the name shown in the accent colour
        "SITE_TAGLINE": env("SITE_TAGLINE", "Your scene, your people: bands, venues and fans in one place."),
        "ACCENT_COLOR": accent_color(env("ACCENT_COLOR")),
        "SITE_URL": env("SITE_URL", "").rstrip("/"),             # public address, used in e-mails
        "OPERATOR_NAME": env("OPERATOR_NAME", "the operator of this site"),
        "OPERATOR_ADDRESS": env("OPERATOR_ADDRESS", ""),
        "CONTACT_EMAIL": env("CONTACT_EMAIL", "mrbacco04@gmail.com"),
        # ---- moderation before publication (docs/MODERATION.md); 0 switches a rule off ----
        # starter blocked-word lists added ONCE, the first time the site starts (curated: it en es fr de pt ru; broad ones such as
        # nl pl tr zh are opt-in on the admin page because they are noisier; "" = none)
        "DEFAULT_WORD_LISTS": env("DEFAULT_WORD_LISTS", "en,it,es,fr,de,pt,ru"),
        "NEW_MEMBER_HOLD_POSTS": int(env("NEW_MEMBER_HOLD_POSTS", 3)),    # a member's first posts wait for a check until this many were approved
        "NEW_MEMBER_HOLD_HOURS": int(env("NEW_MEMBER_HOLD_HOURS", 24)),   # in a new account's first hours, anything with a link or photo waits
        "REPORTS_AUTOHIDE": int(env("REPORTS_AUTOHIDE", 3)),              # this many different reporters hide a post or comment until checked
        "MIN_AGE": int(env("MIN_AGE", 18)),                      # members must be adults (shown at sign-up, in terms and privacy)

        # ---- e-mail (verification, password reset) ----
        "SMTP_HOST": smtp_host,
        "SMTP_PORT": int(env("SMTP_PORT", 587)),
        "SMTP_USER": env("SMTP_USER", ""),
        "SMTP_PASSWORD": env("SMTP_PASSWORD", ""),
        "SMTP_FROM": env("SMTP_FROM", env("CONTACT_EMAIL", "noreply@localhost")),
        "SMTP_SECURITY": env("SMTP_SECURITY", "starttls").lower(),  # starttls | ssl | none
        # "smtp" sends real mail, "console" prints it in the terminal (development), "memory" is for tests
        "MAIL_BACKEND": env("MAIL_BACKEND", "smtp" if smtp_host else "console"),
        # block posting/commenting/messaging until the e-mail address is confirmed (on when mail is configured)
        "REQUIRE_EMAIL_VERIFICATION": env_bool("REQUIRE_EMAIL_VERIFICATION", bool(smtp_host)),

        # ---- photo storage: local folder (default) or any S3-compatible bucket ----
        "S3_BUCKET": env("S3_BUCKET", ""),
        "S3_PREFIX": env("S3_PREFIX", "uploads/"),
        "S3_ENDPOINT_URL": env("S3_ENDPOINT_URL", ""),           # MinIO, Cloudflare R2, Backblaze, ...
        "S3_REGION": env("S3_REGION", ""),

        # ---- "gigs near me": turning a typed place name into coordinates ----
        # nominatim = OpenStreetMap's free service (the typed place text is sent to it), none = off
        "GEOCODER": env("GEOCODER", "nominatim").lower(),
        "GEOCODER_URL": env("GEOCODER_URL", "https://nominatim.openstreetmap.org/search"),

        # ---- gigs imported from Ticketmaster (see docs/EVENT-IMPORT.md): off until a key is set ----
        "TICKETMASTER_API_KEY": env("TICKETMASTER_API_KEY", ""),
        # other providers, each optional (see docs/EVENT-IMPORT.md for what they cover and their terms)
        "SKIDDLE_API_KEY": env("SKIDDLE_API_KEY", ""),         # UK and Ireland
        "PREDICTHQ_API_KEY": env("PREDICTHQ_API_KEY", ""),     # worldwide; paid, with a free trial
        "SONGKICK_API_KEY": env("SONGKICK_API_KEY", ""),       # worldwide, but keys are given out by application only
        # worldwide gigs: the first search around a new place fetches that ~28 km area from the providers
        # (the rounded area is sent to them, never the exact position) and remembers it for IMPORT_COVERAGE_HOURS
        "IMPORT_ON_DEMAND": env_bool("IMPORT_ON_DEMAND", True),
        "IMPORT_COVERAGE_HOURS": int(env("IMPORT_COVERAGE_HOURS", 12)),
        # optional now: areas to keep fresh from cron even when nobody searched there, "Name=latitude,longitude,radius_km" separated by ;
        "IMPORT_AREAS": env("IMPORT_AREAS", ""),
        "IMPORT_DAYS_AHEAD": int(env("IMPORT_DAYS_AHEAD", 120)),
        # the provider only allows short-term storage: events not refreshed within this time are deleted
        "IMPORT_MAX_AGE_HOURS": int(env("IMPORT_MAX_AGE_HOURS", 48)),

        "RATE_LIMITS": _rate_limits(),
        "RATE_LIMITS_ENABLED": env_bool("RATE_LIMITS_ENABLED", True),
    }


def validate(config):
    """Refuse to start in production with an unsafe configuration."""
    if config["APP_ENV"] != "production":
        return
    problems = []
    if config["SECRET_KEY"] == DEFAULT_SECRET or len(config["SECRET_KEY"]) < 32:
        problems.append("SECRET_KEY must be set to a random value of at least 32 characters")
    if not config["SESSION_COOKIE_SECURE"] and not config.get("TESTING"):
        problems.append("SESSION_COOKIE_SECURE must be on (production runs over https)")
    if problems:
        raise RuntimeError(
            "Unsafe production configuration: " + "; ".join(problems) + ".\n"
            "  Running on your own computer?  Set APP_ENV=development in .env (or remove the APP_ENV line).\n"
            "  Running a real server?  Make a key with:  python -c \"import secrets; print(secrets.token_hex(32))\"  "
            "and set SECRET_KEY to it.")
