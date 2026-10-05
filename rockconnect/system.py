# File: system.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-05
"""Technical endpoints: /health for load balancers and uptime monitors, /theme.css for white-labelling."""
from flask import Blueprint, Response, current_app, jsonify

from . import __version__
from .baclog import bac_log
from .db import execute

bp = Blueprint("system", __name__)


@bp.route("/health")
def health():
    """200 when the app and its database answer, 503 otherwise. No sign-in needed, no personal data."""
    try:
        execute("SELECT 1").scalar()
        return jsonify(status="ok", database="ok", version=__version__)
    except Exception as exc:
        bac_log("health", "database check FAILED (%s)" % type(exc).__name__)
        return jsonify(status="error", database="unreachable", version=__version__), 503


def _lighten(hex_color, amount=0.2):
    rgb = [int(hex_color[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(round(c + (255 - c) * amount) for c in rgb)


@bp.route("/theme.css")
def theme():
    """The accent colour of this installation as CSS variables (loaded after style.css).

    Served as a stylesheet rather than an inline <style> tag so the strict Content-Security-Policy holds.
    """
    color = current_app.config["ACCENT_COLOR"]
    rgb = ",".join(str(int(color[i:i + 2], 16)) for i in (1, 3, 5))
    css = ":root {\n    --amber: %s;\n    --amber-hi: %s;\n    --amber-rgb: %s;\n}\n" % (color, _lighten(color), rgb)
    response = Response(css, mimetype="text/css")
    response.headers["Cache-Control"] = "public, max-age=3600"
    return response
