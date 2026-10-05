web: flask --app wsgi db-upgrade && AUTO_MIGRATE=0 gunicorn wsgi:app --workers ${WEB_CONCURRENCY:-3} --threads 2 --timeout 60 --access-logfile - --access-logformat '%(h)s "%(m)s %(U)s" %(s)s %(b)s'
