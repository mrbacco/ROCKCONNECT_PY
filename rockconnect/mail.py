# File: mail.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-05
"""Sending e-mail (account verification and password reset), plain text only.

MAIL_BACKEND (see settings.py):
  smtp     real delivery through SMTP_HOST. Sent from a background thread so the page is not slowed down
           and the response time never reveals whether an address is registered.
  console  development: the message is printed in the terminal (including the link it carries).
  memory   tests: messages are collected in app.extensions["outbox"].

Operator tools: `flask send-test-mail <address>` sends one message and shows the server's answer;
`flask doctor --online` logs in to the mail server and looks up the SPF / DMARC records of the sending domain.
"""
import json
import smtplib
import threading
import urllib.parse
import urllib.request
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr

from flask import current_app

from .baclog import bac_log

DNS_JSON_URL = "https://dns.google/resolve"            # DNS over HTTPS, so the check needs no extra package


def sender_domain(cfg):
    """The domain of SMTP_FROM ('' when it has none)."""
    return parseaddr(cfg["SMTP_FROM"])[1].rpartition("@")[2].lower()


def build(cfg, to, subject, body):
    message = EmailMessage()
    message["From"] = cfg["SMTP_FROM"]
    message["To"] = to
    message["Subject"] = subject
    # spam filters mark mail down without these two
    message["Date"] = formatdate(localtime=False, usegmt=True)
    message["Message-ID"] = make_msgid(domain=sender_domain(cfg) or None)
    message.set_content(body)
    return message


def _connect(cfg):
    """An open, logged-in SMTP connection (the caller closes it)."""
    if cfg["SMTP_SECURITY"] == "ssl":
        server = smtplib.SMTP_SSL(cfg["SMTP_HOST"], cfg["SMTP_PORT"], timeout=15)
    else:
        server = smtplib.SMTP(cfg["SMTP_HOST"], cfg["SMTP_PORT"], timeout=15)
    try:
        if cfg["SMTP_SECURITY"] == "starttls":
            server.starttls()
        if cfg["SMTP_USER"]:
            server.login(cfg["SMTP_USER"], cfg["SMTP_PASSWORD"])
    except Exception:
        server.close()
        raise
    return server


def describe_failure(exc):
    """Why delivery failed, for the operator: the server's own answer, but never a member's address."""
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        return "SMTPRecipientsRefused: the server refused the address"
    if isinstance(exc, smtplib.SMTPResponseException):
        text = exc.smtp_error.decode("utf-8", "replace") if isinstance(exc.smtp_error, bytes) else str(exc.smtp_error)
        return "%s: %s %s" % (type(exc).__name__, exc.smtp_code, " ".join(text.split())[:200])
    return "%s: %s" % (type(exc).__name__, exc)


def deliver(cfg, message):
    """Send now; raises when the server says no."""
    with _connect(cfg) as server:
        server.send_message(message)


def _deliver_smtp(cfg, message):
    try:
        deliver(cfg, message)
        bac_log("mail", "sent '%s' via SMTP" % message["Subject"])
    except Exception as exc:  # never crash a request because the mail server is down
        bac_log("mail", "SMTP delivery FAILED (%s)" % describe_failure(exc))


def send(to, subject, body):
    """Queue one e-mail. Returns True when it was handed to the backend."""
    app = current_app._get_current_object()  # type: ignore[attr-defined]  # the real app, for the background sender
    cfg = app.config
    backend = cfg["MAIL_BACKEND"]
    if backend == "memory":
        app.extensions.setdefault("outbox", []).append({"to": to, "subject": subject, "body": body})
        return True
    if backend == "console":
        bac_log("mail", "CONSOLE MAIL (nothing is sent, set SMTP_HOST to send real mail)")
        print("-" * 60 + "\nTo: %s\nSubject: %s\n\n%s\n" % (to, subject, body) + "-" * 60, flush=True)
        return True
    threading.Thread(target=_deliver_smtp, args=(dict(cfg), build(cfg, to, subject, body)), daemon=True).start()
    return True


# ------------------------------------------------------------------ checks for the operator
def check_login(cfg):
    """(ok, text): connect and log in to the mail server without sending anything."""
    try:
        with _connect(cfg) as server:
            server.noop()
    except Exception as exc:
        return False, "cannot use %s:%s (%s)" % (cfg["SMTP_HOST"], cfg["SMTP_PORT"], describe_failure(exc))
    return True, "logged in to %s:%s" % (cfg["SMTP_HOST"], cfg["SMTP_PORT"])


def _txt_records(name):
    query = urllib.parse.urlencode({"name": name, "type": "TXT"})
    request = urllib.request.Request(DNS_JSON_URL + "?" + query, headers={"Accept": "application/dns-json"})
    with urllib.request.urlopen(request, timeout=10) as response:
        answer = json.load(response).get("Answer") or []
    # a long TXT record comes back as several quoted pieces: join them
    return ["".join(part for part in a["data"].split('"') if part.strip()) for a in answer if a.get("type") == 16]


def check_dns(domain):
    """[(label, text, problem)] for the SPF and DMARC records of the sending domain. DKIM needs the provider's
    selector name, so it is not looked up here (the provider's dashboard says whether it sees the record)."""
    rows = []
    for label, name, prefix in (("SPF record", domain, "v=spf1"), ("DMARC record", "_dmarc." + domain, "v=DMARC1")):
        try:
            found = [r for r in _txt_records(name) if r.lower().startswith(prefix.lower())]
        except (OSError, ValueError) as exc:
            rows.append((label, "could not look it up (%s)" % type(exc).__name__, True))
            continue
        if len(found) > 1 and prefix == "v=spf1":
            rows.append((label, "%d SPF records on %s: there must be exactly one, merge them" % (len(found), name), True))
        elif found:
            rows.append((label, found[0][:120], False))
        else:
            rows.append((label, "NONE on %s: mail will likely land in spam (your mail provider shows what to add)" % name,
                         True))
    return rows
