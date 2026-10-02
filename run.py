# File: run.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-02
import os

from rockconnect import create_app

from rockconnect.baclog import bac_log

app = create_app()

if __name__ == "__main__":
    # same defaults as the original node app: 0.0.0.0:3000
    host = os.environ.get("IP", "0.0.0.0")
    port = int(os.environ.get("PORT", 3000))
    bac_log("run", "rockconnect is running on http://localhost:%d (bound to %s)" % (port, host))
    app.run(host=host, port=port, debug=os.environ.get("FLASK_DEBUG") == "1")
