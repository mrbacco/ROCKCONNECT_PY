# File: legal.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-05
"""Terms of service, privacy policy and cookie notice.

The wording lives in templates/legal_*.html and is filled in with the operator's details from the
environment (OPERATOR_NAME, OPERATOR_ADDRESS, CONTACT_EMAIL, MIN_AGE, SITE_NAME). It is a sound starting
point, not legal advice: the operator must have it reviewed for their own situation.
"""
from flask import Blueprint, render_template

bp = Blueprint("legal", __name__)


@bp.route("/terms")
def terms():
    return render_template("legal_terms.html")


@bp.route("/privacy")
def privacy():
    return render_template("legal_privacy.html")


@bp.route("/cookies")
def cookies():
    return render_template("legal_cookies.html")
