# File: Dockerfile
# Author: mrbacco04@gmail.com
# Date: 2026-10-05
# Production image: gunicorn, non-root user, PostgreSQL and S3 drivers included.
#   docker build -t rockconnect .
#   docker run -p 8000:8000 --env-file .env -v rockconnect-data:/app/instance rockconnect
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_ENV=production \
    PORT=8000

WORKDIR /app

# dependencies first, so rebuilding after a code change reuses this layer
COPY requirements.txt requirements-docker.txt ./
RUN pip install --no-cache-dir -r requirements-docker.txt

COPY . .

# never run as root; /app/instance holds the SQLite file and photos when no DATABASE_URL / S3 is configured
RUN useradd --create-home --uid 1000 app \
    && mkdir -p /app/instance \
    && chown -R app:app /app/instance \
    && chmod +x /app/docker/entrypoint.sh
USER app
VOLUME /app/instance
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ.get('PORT', '8000'), timeout=4)"

ENTRYPOINT ["/app/docker/entrypoint.sh"]
