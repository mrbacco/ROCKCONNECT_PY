#!/bin/sh
# File: entrypoint.sh
# Author: mrbacco04@gmail.com
# Date: 2026-10-05
# Container start: migrate the database once, then start the web workers.
set -e

echo "rockconnect: applying database migrations..."
flask --app wsgi db-upgrade

# the migration above already ran: the workers must not all try it again at the same moment
export AUTO_MIGRATE=0

# The access log prints the path WITHOUT the query string (only the path, never the full request line): "gigs near me" requests carry the
# member's position in the query string and that must not end up in log files.
echo "rockconnect: starting gunicorn on port ${PORT:-8000}"
exec gunicorn wsgi:app \
    --bind "0.0.0.0:${PORT:-8000}" \
    --workers "${WEB_CONCURRENCY:-3}" \
    --threads "${GUNICORN_THREADS:-2}" \
    --timeout 60 \
    --access-logfile - \
    --access-logformat '%(h)s "%(m)s %(U)s" %(s)s %(b)s %(L)ss'
