#!/bin/sh
# One command to running: migrate, seed the fixtures, print the auth headers, serve.
set -eu

cd /app

echo "[plumbline] applying migrations"
python manage.py migrate --noinput
python manage.py createcachetable

if [ "${PLUMBLINE_SEED:-1}" = "1" ]; then
  echo "[plumbline] seeding ${PLUMBLINE_FIXTURES:-fixtures.json}"
  python manage.py seed_fixtures "${PLUMBLINE_FIXTURES:-fixtures.json}"
fi

echo "[plumbline] serving on http://0.0.0.0:8080"
exec gunicorn plumbline.wsgi:application \
  --bind 0.0.0.0:8080 \
  --workers "${GUNICORN_WORKERS:-2}" \
  --access-logfile - \
  --error-logfile -
