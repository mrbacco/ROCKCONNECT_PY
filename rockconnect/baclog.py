# File: baclog.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-02
"""BAC_LOG: tiny print-to-terminal logger used across the app.

Every line looks like:  BAC_LOG | 2026-10-02 23:15:01 | <module> | <message>
NEVER pass passwords, password hashes or CSRF tokens to bac_log.
"""
import os
from datetime import datetime

# set BAC_LOG=0 in the environment to silence the logs (e.g. noisy test runs)
ENABLED = os.environ.get("BAC_LOG", "1") != "0"


def bac_log(module, message):
    """Print one BAC_LOG line to the terminal (flush so it shows up immediately)."""
    if not ENABLED:
        return
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print("BAC_LOG | %s | %s | %s" % (stamp, module, message), flush=True)
