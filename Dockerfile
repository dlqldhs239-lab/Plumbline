FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends libpq5 \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

RUN chmod +x docker/entrypoint.sh \
 && DJANGO_SECRET_KEY=build-only python manage.py collectstatic --noinput

EXPOSE 8080
ENTRYPOINT ["/app/docker/entrypoint.sh"]
