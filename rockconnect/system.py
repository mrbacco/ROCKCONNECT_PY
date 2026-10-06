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


def _luminance(rgb):
    """Relative luminance (WCAG) of an (r, g, b) tuple of 0-255 values."""
    def channel(c):
        c /= 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (channel(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(a, b):
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def readable_colors(hex_color):
    """(text colour for use ON the accent, accent shade for use as TEXT on white). A pale yellow cannot be read on white,
    so the text version is darkened until it can; text on a pale accent is dark, on a deep accent white."""
    rgb = tuple(int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    on_accent = "#16161a" if _contrast(rgb, (22, 22, 26)) >= _contrast(rgb, (255, 255, 255)) else "#ffffff"
    ink = rgb
    for step in range(21):
        ink = tuple(round(c * (1 - step * 0.04)) for c in rgb)
        if _contrast(ink, (255, 255, 255)) >= 4.5:
            break
    return on_accent, "#%02x%02x%02x" % ink


@bp.route("/theme.css")
def theme():
    """The accent colour of this installation as CSS variables (loaded after style.css).

    Served as a stylesheet rather than an inline <style> tag so the strict Content-Security-Policy holds.
    """
    color = current_app.config["ACCENT_COLOR"]
    rgb = ",".join(str(int(color[i:i + 2], 16)) for i in (1, 3, 5))
    on_accent, ink = readable_colors(color)
    css = (":root {\n    --amber: %s;\n    --amber-hi: %s;\n    --amber-rgb: %s;\n    --on-accent: %s;\n    --accent-ink: %s;\n}\n"
           % (color, _lighten(color), rgb, on_accent, ink))
    response = Response(css, mimetype="text/css")
    response.headers["Cache-Control"] = "public, max-age=3600"
    return response
