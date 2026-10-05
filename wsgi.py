# File: wsgi.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-05
"""Entry point for production servers:  gunicorn wsgi:app   (or waitress-serve wsgi:app on Windows)."""
from rockconnect import create_app, envfile

envfile.load()   # settings from the .env file next to this one (real environment variables win)
app = create_app()
