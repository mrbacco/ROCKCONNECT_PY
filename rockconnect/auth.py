# File: auth.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-05
"""Sign up, sign in, sign out, e-mail confirmation, password reset + session handling.

Flow:  1. register (/users/add)         with REQUIRE_EMAIL_VERIFICATION: keeps the sign-up and e-mails a link; the account is
                                        created when the link (/users/confirm/<token>) is opened. Without it (development): creates
                                        the account at once and e-mails a confirmation link
       2. sign in (/users/signin)       creates a server-side session (30 days of inactivity by default)
       3. every request                 checks the session is alive and slides its end date forward
       4. long absence, sign out, password change or suspension: the user signs in again (and returns to
          where they were)
Forgotten password: /users/forgot e-mails a one-time link to /users/reset/<token>.
"""
import functools
import secrets

import bcrypt
from flask import (Blueprint, current_app, flash, g, jsonify, redirect, render_template,
                   request, session, url_for)
from sqlalchemy.exc import IntegrityError

from . import ages, mail, ratelimit, session_store, tokens
from .baclog import bac_log
from .db import KINDS, commit, execute, insert, pending_signups, rollback, users
from .util import (client_ip, external_url, in_minutes, now_str, safe_next, sha256, valid_email,
                   valid_username)

bp = Blueprint("auth", __name__, url_prefix="/users")

FIELDS = ("username", "name", "email", "password", "about")
MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_BYTES = 72  # bcrypt only looks at the first 72 bytes, so longer ones are refused up front

# endpoints that need no session lookup (and no database query)
NO_SESSION_ENDPOINTS = ("static", "system.health", "system.theme")

# the most used passwords: refused whatever else they satisfy
COMMON_PASSWORDS = frozenset("""
password 12345678 123456789 1234567890 qwertyui qwertyuiop 11111111 00000000 iloveyou password1 password123
abc12345 abcd1234 1q2w3e4r 1qaz2wsx letmein1 welcome1 admin123 football baseball superman trustno1
passw0rd p@ssw0rd p@ssword monkey12 dragon12 master12 sunshine princess starwars changeme rockandroll
rockconnect metallica acdc1234 ironmaiden nirvana1 guitar123 whatever
""".split())


def hash_password(password):
    # bcrypt with cost factor 10 (same as bcryptjs genSalt(10) in the original)
    bac_log("auth", "hashing password with bcrypt (cost 10)")
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(10)).decode()


def check_password(password, hashed):
    # compares the typed password with the stored hash; never logs either value
    encoded = password.encode()
    if len(encoded) > MAX_PASSWORD_BYTES:
        return False  # cannot match a bcrypt hash made by this app, and bcrypt refuses to look at it
    ok = bcrypt.checkpw(encoded, hashed.encode())
    bac_log("auth", "bcrypt password check -> %s" % ("match" if ok else "NO match"))
    return ok


def password_error(password, username="", email=""):
    """Why this password is not acceptable (text for the user), or None when it is fine."""
    if len(password) < MIN_PASSWORD_LENGTH:
        return "Password must be at least %d characters." % MIN_PASSWORD_LENGTH
    if len(password.encode()) > MAX_PASSWORD_BYTES:
        return "Password is too long (at most %d bytes)." % MAX_PASSWORD_BYTES
    lowered = password.lower()
    if lowered in COMMON_PASSWORDS or len(set(lowered)) < 3:
        return "That password is too easy to guess. Pick something less common."
    if lowered in (username.lower(), email.lower(), email.lower().split("@")[0]):
        return "Your password must not be your username or email."
    return None


def _wants_json():
    # chat.js / main.js send this header; plain page loads and HTML forms do not
    return request.headers.get("X-Requested-With") == "fetch"


def login_required(view):
    """Decorator: no valid session -> sign-in page (HTML) or 401 JSON (for the background scripts)."""
    @functools.wraps(view)
    def wrapped(**kwargs):
        if g.user is None:
            bac_log("auth", "login_required: no valid session for %s %s -> sign in"
                    % (request.method, request.path))
            if _wants_json():
                # the page's scripts see this and send the browser to the sign-in page
                return jsonify(error="session_expired", login_url=url_for("auth.signin")), 401
            if not g.session_expired:  # an expired session already queued its own message
                flash("Please sign in first.", "warning")
            # remember where the user was (GET only: a POST cannot be replayed)
            target = request.full_path.rstrip("?") if request.method == "GET" else None
            return redirect(url_for("auth.signin", next=target))
        return view(**kwargs)

    return wrapped


def verified_required(view):
    """Decorator (use under @login_required): when the operator requires confirmed e-mail addresses,
    posting, commenting and messaging wait until the member has clicked the link in their mail."""
    @functools.wraps(view)
    def wrapped(**kwargs):
        if current_app.config["REQUIRE_EMAIL_VERIFICATION"] and not g.user["email_verified"]:
            bac_log("auth", "user id=%s blocked: e-mail not confirmed yet" % g.user["id"])
            message = "Please confirm your email address first (check your inbox)."
            if _wants_json():
                return jsonify(error=message), 403
            flash(message, "warning")
            return redirect(url_for("account.settings"))
        return view(**kwargs)

    return wrapped


@bp.before_app_request
def load_logged_in_user():
    """Runs before every request: finds the session behind the cookie and sets g.user (or None)."""
    g.user = None
    g.session_expired = False
    if request.endpoint in NO_SESSION_ENDPOINTS:
        return  # css/js/images and health checks need no session (and no database query)
    token = session.get("sid")
    if not token:
        return
    user = session_store.find_valid(token)
    if user is None:
        # the cookie is still there but the server session ended (expired or signed out elsewhere)
        session.pop("sid", None)
        g.session_expired = True
        flash("Your session has expired. Please sign in again.", "warning")
        bac_log("session", "expired or unknown session cookie -> user must sign in again")
        return
    if user["status"] == "banned":
        # an admin suspended this account while it was signed in: end the session right away
        session_store.destroy(token)
        session.pop("sid", None)
        g.session_expired = True
        flash("This account has been suspended.", "danger")
        bac_log("session", "banned user id=%s was signed out" % user["id"])
        return
    g.user = user
    session_store.renew(token, user["session_expires_at"])  # visiting keeps you signed in


# what a member without a date of birth may still reach: the page asking for it, signing out, the legal pages and the
# rights over their own data
AGE_GATE_OPEN = {"account.confirm_age", "auth.signout", "account.export", "account.delete", "legal.terms",
                 "legal.privacy", "legal.cookies", "static", "system.health", "system.theme"}


@bp.before_app_request
def age_gate():
    """Members who joined before the date of birth was asked for must give it once, before anything else."""
    if g.user is None or g.user["birth_date"] is not None or request.endpoint in AGE_GATE_OPEN or request.endpoint is None:
        return None
    bac_log("auth", "user id=%s has no date of birth yet -> asked once" % g.user["id"])
    if request.path.startswith("/api/") or _wants_json():
        return jsonify(error="Please confirm your date of birth first.", code="age_required"), 403
    return redirect(url_for("account.confirm_age", next=request.full_path.rstrip("?") if request.method == "GET" else None))


# throw-away mailbox services: a link sent there proves nothing about the person, so they are refused (operators add more
# with BLOCKED_EMAIL_DOMAINS)
DISPOSABLE_DOMAINS = frozenset("""
mailinator.com guerrillamail.com guerrillamail.net guerrillamail.org guerrillamail.biz guerrillamail.de sharklasers.com grr.la
10minutemail.com 10minutemail.net tempmail.com temp-mail.org temp-mail.io tempmailo.com yopmail.com yopmail.net yopmail.fr
trashmail.com trashmail.net throwawaymail.com getnada.com dispostable.com maildrop.cc fakeinbox.com mintemail.com mohmal.com
tempr.email emailondeck.com spamgourmet.com mailnesia.com burnermail.io discard.email moakt.com tmpmail.org tmpmail.net
fakemailgenerator.com getairmail.com mailcatch.com spambox.us trashmail.de wegwerfemail.de einrot.com mytemp.email
""".split())
PENDING_MINUTES = 60 * 24


def blocked_email_domain(email):
    """True when the address is on a throw-away mailbox service (or a domain the operator blocked)."""
    domain = email.rsplit("@", 1)[-1].lower()
    extra = {d.strip().lower() for d in str(current_app.config.get("BLOCKED_EMAIL_DOMAINS", "")).split(",") if d.strip()}
    return domain in DISPOSABLE_DOMAINS or domain in extra


def mask_email(email):
    """'rita.moran@example.com' -> 'r*******@example.com' (shown to someone who proved they know the password)."""
    name, _, domain = email.partition("@")
    return name[:1] + "*" * max(len(name) - 1, 1) + "@" + domain


def send_signup_link(pending):
    """Mail the link that creates the account. `pending` is the new row's values plus the clear token."""
    site = current_app.config["SITE_NAME"]
    mail.send(pending["email"], "Confirm your email to join %s" % site,
              "Hi %s,\n\nOne more step to join %s: open the link below to confirm that this email address is yours. "
              "Your account is created when you do.\n\n%s\n\nThe link works once and expires in 24 hours. "
              "If you did not sign up, ignore this message: nothing is created and your address is forgotten."
              % (pending["name"], site, external_url("auth.confirm_signup", token=pending["token"])))


def start_pending_signup(form):
    """Keep a valid sign-up until its link is opened, and mail the link. Any older waiting sign-up for the same address
    is replaced (only the newest link works)."""
    execute("DELETE FROM pending_signups WHERE expires_at < :now", now=now_str())
    execute("DELETE FROM pending_signups WHERE email = :e", e=form["email"])
    token = secrets.token_urlsafe(32)
    insert(pending_signups, token_hash=sha256(token), username=form["username"], name=form["name"], email=form["email"],
           password=hash_password(form["password"]), about=form["about"], kind=form["kind"], birth_date=form["birth_date"],
           terms_accepted_at=now_str(), created_at=now_str(), expires_at=in_minutes(PENDING_MINUTES))
    commit()
    send_signup_link(dict(form, token=token))
    bac_log("signup", "pending sign-up for %r waits for its confirmation link" % form["username"])


def send_verification(user_id, current_email, new_email=None):
    """E-mail a confirmation link to the address being confirmed."""
    token = tokens.create(user_id, "verify", new_email)
    site = current_app.config["SITE_NAME"]
    mail.send(new_email or current_email, "Confirm your email for %s" % site,
              "Welcome to %s!\n\nConfirm this email address by opening the link below "
              "(it works once and expires in 24 hours):\n\n%s\n\nIf you did not ask for this, ignore this message."
              % (site, external_url("auth.verify", token=token)))


@bp.route("/add", methods=("GET", "POST"))
def signup():
    form = {}
    if request.method == "POST":
        ratelimit.allow("signup_ip", client_ip())
        # collect the registration fields (password is kept exactly as typed)
        form = {f: request.form.get(f, "").strip() for f in FIELDS}
        form["password"] = request.form.get("password", "")  # keep as typed
        form["email"] = form["email"].lower()
        form["kind"] = request.form.get("kind", "fan")
        form["accept"] = request.form.get("accept", "")
        form["birth_date"] = request.form.get("birth_date", "").strip()
        bac_log("signup", "attempt for username=%r kind=%s" % (form["username"], form["kind"]))
        missing = [f for f in FIELDS if not form[f]]
        error = None
        if missing:
            error = "All fields are required (missing: %s)." % ", ".join(missing)
        elif not valid_username(form["username"]):
            error = "Username must be 3-30 characters: letters, digits, dot, dash or underscore."
        elif not valid_email(form["email"]):
            error = "That does not look like an email address."
        elif blocked_email_domain(form["email"]):
            error = "Please use a permanent email address: throw-away mailboxes cannot be used to join."
        elif form["kind"] not in KINDS:
            error = "Choose fan, band or venue."
        elif len(form["name"]) > 120 or len(form["about"]) > 1000:
            error = "Name or about text is too long."
        elif password_error(form["password"], form["username"], form["email"]):
            error = password_error(form["password"], form["username"], form["email"])
        elif ages.error_for(form["birth_date"]):
            error = ages.error_for(form["birth_date"])
        elif form["accept"] != "1":
            error = "You must accept the terms and confirm that your date of birth is true."
        if not error and current_app.config["REQUIRE_EMAIL_VERIFICATION"] and execute(
                "SELECT 1 FROM users WHERE lower(email) = :e OR username = :u", e=form["email"], u=form["username"]).fetchone():
            error = "That username or email is already registered."
        if error:
            bac_log("signup", "rejected: %s" % error)
            flash(error, "danger")
        elif current_app.config["REQUIRE_EMAIL_VERIFICATION"]:
            # confirm first: no account exists until the link sent to this address is opened
            start_pending_signup(form)
            return render_template("check_email.html", email=form["email"], masked=mask_email(form["email"]))
        else:
            try:
                new_id = insert(
                    users, username=form["username"], name=form["name"], email=form["email"],
                    password=hash_password(form["password"]), about=form["about"], kind=form["kind"],
                    terms_accepted_at=now_str(), created_at=now_str(), birth_date=form["birth_date"],
                )
                commit()
            except IntegrityError:
                # UNIQUE constraint on username or email was violated
                rollback()  # a failed statement leaves the transaction unusable on PostgreSQL
                bac_log("signup", "rejected, duplicate username/email for %r" % form["username"])
                flash("That username or email is already registered.", "danger")
            else:
                # registering only creates the account; the session starts when the user signs in
                bac_log("signup", "user %r created (id=%s), now needs to sign in"
                        % (form["username"], new_id))
                send_verification(new_id, form["email"])
                flash("Welcome to %s, %s! Your account is ready, please sign in. We sent a link to "
                      "%s to confirm your email address."
                      % (current_app.config["SITE_NAME"], form["name"], form["email"]), "success")
                return redirect(url_for("auth.signin"))
    form.pop("password", None)
    return render_template("add_users.html", title="rockconnect", form=form)


@bp.route("/signin", methods=("GET", "POST"))
def signin():
    # where to go after signing in (set when a protected page sent the user here)
    next_url = request.values.get("next", "")
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        bac_log("signin", "attempt for username=%r" % username)
        # too many recent failures from this address or for this username: refuse before touching the password
        ratelimit.check("signin_ip", client_ip())
        ratelimit.check("signin_user", username)
        user = execute(
            "SELECT * FROM users WHERE username = :username", username=username
        ).mappings().fetchone()
        if user is None:
            waiting = execute("SELECT * FROM pending_signups WHERE username = :u AND expires_at > :now ORDER BY created_at DESC",
                              u=username, now=now_str()).mappings().fetchone()
            if waiting is not None and check_password(password, waiting["password"]):
                # someone who knows the password of a sign-up that was never confirmed: tell them what is missing
                bac_log("signin", "refused: sign-up of %r is not confirmed yet" % username)
                return render_template("check_email.html", email=waiting["email"], masked=mask_email(waiting["email"]),
                                       came_from_signin=True), 403
        if user is None or not check_password(password, user["password"]):
            # same message for unknown user and wrong password (no user enumeration)
            bac_log("signin", "FAILED for username=%r (unknown user or bad password)" % username)
            ratelimit.hit("signin_ip", client_ip())
            ratelimit.hit("signin_user", username)
            flash("Invalid credentials.", "danger")
            return render_template("signin_users.html", title="rockconnect", next_url=next_url), 401
        if user["status"] == "banned":
            bac_log("signin", "refused: user id=%s is banned" % user["id"])
            flash("This account has been suspended.%s" % (
                " Reason: %s" % user["ban_reason"] if user["ban_reason"] else ""), "danger")
            return render_template("signin_users.html", title="rockconnect", next_url=next_url), 403
        session_store.destroy(session.get("sid"))  # end any older session of this browser
        session.clear()                            # fresh cookie (prevents session fixation)
        token, expires_at = session_store.create(user["id"])
        session["sid"] = token
        session.permanent = True                   # cookie outlives the login (see create_app) so expiry can be explained
        bac_log("signin", "SUCCESS user=%r id=%s, session valid until %s UTC"
                % (user["username"], user["id"], expires_at))
        flash("Signed in as %s." % user["username"], "success")
        return redirect(safe_next(next_url, url_for("feed.index")))
    return render_template("signin_users.html", title="rockconnect", next_url=next_url)


@bp.route("/signout", methods=("POST",))
def signout():
    bac_log("signout", "user id=%s signs out" % (g.user["id"] if g.user else None))
    session_store.destroy(session.get("sid"))  # the session dies on the server, not just in the browser
    session.clear()
    flash("You have been signed out.", "info")
    return redirect(url_for("views.home"))


# ------------------------------------------------------------------ e-mail confirmation
@bp.route("/verify/<token>", methods=("GET", "POST"))
def verify(token):
    """The link in the confirmation mail. GET only shows a button, the POST does the work, so mail
    scanners that pre-open links cannot use the link up."""
    row = tokens.peek(token, "verify")
    if row is None:
        bac_log("verify", "invalid or expired confirmation link")
        flash("That confirmation link is invalid or has expired. Sign in and ask for a new one.", "warning")
        return redirect(url_for("auth.signin"))
    if request.method == "POST":
        row = tokens.consume(token, "verify")
        if row is None:
            flash("That confirmation link was already used.", "warning")
            return redirect(url_for("auth.signin"))
        try:
            if row["new_email"]:
                execute("UPDATE users SET email = :e, email_verified = 1 WHERE id = :id",
                        e=row["new_email"], id=row["user_id"])
            else:
                execute("UPDATE users SET email_verified = 1 WHERE id = :id", id=row["user_id"])
            commit()
        except IntegrityError:
            rollback()
            flash("That email address is already used by another account.", "danger")
            return redirect(url_for("account.settings" if g.user else "auth.signin"))
        bac_log("verify", "email confirmed for user id=%s" % row["user_id"])
        flash("Thank you, your email address is confirmed.", "success")
        return redirect(url_for("feed.index" if g.user else "auth.signin"))
    return render_template("verify_email.html", token=token, new_email=row["new_email"])


@bp.route("/confirm/<token>", methods=("GET", "POST"))
def confirm_signup(token):
    """The link in the sign-up mail. GET only shows a button (mail scanners pre-open links), the POST creates the account."""
    row = execute("SELECT * FROM pending_signups WHERE token_hash = :h AND expires_at > :now",
                  h=sha256(token), now=now_str()).mappings().fetchone()
    if row is None:
        bac_log("signup", "invalid or expired sign-up link")
        flash("That link is invalid or has expired. Sign up again and we will send a new one.", "warning")
        return redirect(url_for("auth.signup"))
    if request.method == "GET":
        return render_template("confirm_signup.html", token=token, email=row["email"], name=row["name"])
    execute("DELETE FROM pending_signups WHERE token_hash = :h", h=row["token_hash"])     # used up, whatever happens next
    commit()
    try:
        new_id = insert(users, username=row["username"], name=row["name"], email=row["email"], password=row["password"],
                        about=row["about"], kind=row["kind"], terms_accepted_at=row["terms_accepted_at"],
                        created_at=now_str(), birth_date=row["birth_date"], email_verified=1)
        commit()
    except IntegrityError:
        rollback()
        bac_log("signup", "confirmation failed: username or email taken in the meantime (%r)" % row["username"])
        flash("Sorry, that username or email was registered by someone else while you were confirming. "
              "Please sign up again with a different username.", "danger")
        return redirect(url_for("auth.signup"))
    bac_log("signup", "user %r created (id=%s) by confirming the email link" % (row["username"], new_id))
    flash("Your email is confirmed and your account is ready, %s. Please sign in." % row["name"], "success")
    return redirect(url_for("auth.signin"))


@bp.route("/confirm-resend", methods=("POST",))
def resend_signup_link():
    """'Send the link again' for a sign-up that is not confirmed. The answer is the same whether the address is waiting or
    not, so this cannot be used to find out who is signing up."""
    email = request.form.get("email", "").strip().lower()
    ratelimit.allow("signup_ip", client_ip())
    if valid_email(email):
        ratelimit.allow("pending_resend", email)
        row = execute("SELECT * FROM pending_signups WHERE email = :e AND expires_at > :now", e=email, now=now_str()).mappings().fetchone()
        if row is not None:
            token = secrets.token_urlsafe(32)
            execute("DELETE FROM pending_signups WHERE token_hash = :h", h=row["token_hash"])
            insert(pending_signups, token_hash=sha256(token), username=row["username"], name=row["name"], email=row["email"],
                   password=row["password"], about=row["about"], kind=row["kind"], birth_date=row["birth_date"],
                   terms_accepted_at=row["terms_accepted_at"], created_at=row["created_at"], expires_at=in_minutes(PENDING_MINUTES))
            commit()
            send_signup_link(dict(row, token=token))
    flash("If that address is waiting for confirmation, we sent a new link to it.", "info")
    return render_template("check_email.html", email=email, masked=mask_email(email) if "@" in email else email)


@bp.route("/verify", methods=("POST",))
@login_required
def resend_verification():
    """'Send me the confirmation mail again' button."""
    if g.user["email_verified"]:
        flash("Your email address is already confirmed.", "info")
    else:
        ratelimit.allow("verify_user", g.user["id"])
        send_verification(g.user["id"], g.user["email"])
        flash("We sent a new confirmation link to %s." % g.user["email"], "info")
    return redirect(safe_next(request.form.get("next"), url_for("account.settings")))


# ------------------------------------------------------------------ forgotten password
@bp.route("/forgot", methods=("GET", "POST"))
def forgot():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        ratelimit.allow("forgot_ip", client_ip())
        ratelimit.allow("forgot_email", email)
        user = execute("SELECT id, email, status FROM users WHERE lower(email) = :e", e=email).mappings().fetchone()
        if user is not None and user["status"] == "active":
            token = tokens.create(user["id"], "reset")
            site = current_app.config["SITE_NAME"]
            mail.send(user["email"], "Reset your %s password" % site,
                      "Someone asked to reset the password of your %s account.\n\n"
                      "Choose a new password here (the link works once and expires in 1 hour):\n\n%s\n\n"
                      "If this was not you, ignore this message: your password stays as it is."
                      % (site, external_url("auth.reset", token=token)))
            bac_log("forgot", "reset mail queued for user id=%s" % user["id"])
        else:
            bac_log("forgot", "no active account for that address (nothing sent)")
        # the same answer whether or not the address is registered (no account enumeration)
        flash("If that address belongs to an account, we sent a link to reset the password.", "info")
        return redirect(url_for("auth.signin"))
    return render_template("forgot.html")


@bp.route("/reset/<token>", methods=("GET", "POST"))
def reset(token):
    row = tokens.peek(token, "reset")
    if row is None:
        flash("That reset link is invalid or has expired. Please ask for a new one.", "warning")
        return redirect(url_for("auth.forgot"))
    if request.method == "POST":
        password = request.form.get("password", "")
        user = execute("SELECT username, email FROM users WHERE id = :id", id=row["user_id"]).mappings().fetchone()
        error = password_error(password, user["username"], user["email"])
        if password != request.form.get("password2", ""):
            error = "The two passwords do not match."
        if error:
            flash(error, "danger")
            return render_template("reset.html", token=token)
        if tokens.consume(token, "reset") is None:
            flash("That reset link was already used.", "warning")
            return redirect(url_for("auth.forgot"))
        # a reset proves the owner reads this mailbox, so the address counts as confirmed too
        execute("UPDATE users SET password = :p, email_verified = 1 WHERE id = :id",
                p=hash_password(password), id=row["user_id"])
        session_store.destroy_all(row["user_id"])  # whoever had the old password is signed out everywhere
        commit()
        bac_log("reset", "password changed for user id=%s, all sessions ended" % row["user_id"])
        flash("Your password was changed. Please sign in.", "success")
        return redirect(url_for("auth.signin"))
    return render_template("reset.html", token=token)
