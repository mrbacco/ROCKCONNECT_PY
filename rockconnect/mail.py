# File: mail.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-05
"""Sending e-mail (account verification and password reset), plain text only.

MAIL_BACKEND (see settings.py):
  smtp     real delivery through SMTP_HOST. Sent from a background thread so the page is not slowed down
           and the response time never reveals whether an address is registered.
  console  development: the message is printed in the terminal (including the link it carries).
  memory   tests: messages are collected in app.extensions["outbox"].
"""
import smtplib
import threading
from email.message import EmailMessage

from flask import current_app

from .baclog import bac_log


def _deliver_smtp(cfg, message):
    try:
        if cfg["SMTP_SECURITY"] == "ssl":
            server = smtplib.SMTP_SSL(cfg["SMTP_HOST"], cfg["SMTP_PORT"], timeout=15)
        else:
            server = smtplib.SMTP(cfg["SMTP_HOST"], cfg["SMTP_PORT"], timeout=15)
        with server:
            if cfg["SMTP_SECURITY"] == "starttls":
                server.starttls()
            if cfg["SMTP_USER"]:
                server.login(cfg["SMTP_USER"], cfg["SMTP_PASSWORD"])
            server.send_message(message)
        bac_log("mail", "sent '%s' via SMTP" % message["Subject"])
    except Exception as exc:  # never crash a request because the mail server is down
        bac_log("mail", "SMTP delivery FAILED (%s)" % type(exc).__name__)


def send(to, subject, body):
    """Queue one e-mail. Returns True when it was handed to the backend."""
    app = current_app._get_current_object()
    cfg = app.config
    backend = cfg["MAIL_BACKEND"]
    if backend == "memory":
        app.extensions.setdefault("outbox", []).append({"to": to, "subject": subject, "body": body})
        return True
    if backend == "console":
        bac_log("mail", "CONSOLE MAIL (nothing is sent, set SMTP_HOST to send real mail)")
        print("-" * 60 + "\nTo: %s\nSubject: %s\n\n%s\n" % (to, subject, body) + "-" * 60, flush=True)
        return True

    message = EmailMessage()
    message["From"] = cfg["SMTP_FROM"]
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)
    threading.Thread(target=_deliver_smtp, args=(dict(cfg), message), daemon=True).start()
    return True
