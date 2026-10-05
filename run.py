# File: run.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-05
"""Start the server on this machine.

Development: python run.py            (Flask's own server, set FLASK_DEBUG=1 for auto-reload)
Production:  APP_ENV=production python run.py   (waitress, works on Windows too)
On Linux a container or server normally uses gunicorn instead, see Dockerfile and docs/DEPLOY.md.
"""
import os

from rockconnect import create_app, envfile

from rockconnect.baclog import bac_log

envfile.load()   # settings from the .env file next to this one (real environment variables win)
app = create_app()

if __name__ == "__main__":
    # same defaults as the original node app: 0.0.0.0:3000
    host = os.environ.get("IP", "0.0.0.0")
    port = int(os.environ.get("PORT", 3000))
    debug = os.environ.get("FLASK_DEBUG") == "1"
    bac_log("run", "rockconnect is running on http://localhost:%d (bound to %s)" % (port, host))
    if app.config["APP_ENV"] == "production" and not debug:
        from waitress import serve
        serve(app, host=host, port=port, threads=8)
    else:
        app.run(host=host, port=port, debug=debug)
