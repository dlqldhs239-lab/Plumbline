#!/bin/sh
# One command to running: migrate, seed the fixtures, print the auth headers, serve.
set -eu

cd /app

echo "[plumbline] applying migrations"
python manage.py migrate --noinput
python manage.py createcachetable

if [ "${PLUMBLINE_SEED:-1}" = "1" ]; then
  echo "[plumbline] seeding ${PLUMBLINE_FIXTURES:-fixtures.json}"
  # The sample event closed months ago and every review is in, so it is
  # loaded as a finished event: results computed and published.
  if [ "${PLUMBLINE_SEED_PUBLISH:-1}" = "1" ]; then
    python manage.py seed_fixtures "${PLUMBLINE_FIXTURES:-fixtures.json}" --publish
  else
    python manage.py seed_fixtures "${PLUMBLINE_FIXTURES:-fixtures.json}"
  fi
fi

echo "[plumbline] serving on http://0.0.0.0:8080"
# Threaded workers, because the portal is reached by browsers directly. A
# browser opens spare connections and leaves them idle; with the default sync
# worker each idle connection blocks a whole worker until it times out, and
# two of them make the site stop answering.
exec gunicorn plumbline.wsgi:application \
  --bind 0.0.0.0:8080 \
  --worker-class gthread \
  --workers "${GUNICORN_WORKERS:-2}" \
  --threads "${GUNICORN_THREADS:-8}" \
  --keep-alive 5 \
  --timeout 60 \
  --access-logfile - \
  --error-logfile -
