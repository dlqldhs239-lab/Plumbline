"""Plumbline settings.

Everything is driven by environment variables so the same image runs in
`docker compose up` and in a real deployment. Nothing here needs the network.
"""

import os
from pathlib import Path

import dj_database_url

BASE_DIR = Path(__file__).resolve().parent.parent


def env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


DEFAULT_SECRET_KEY = "dev-only-change-me-in-production"
DEFAULT_SEED_SECRET = "dev-seed-secret"
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", DEFAULT_SECRET_KEY)
DEBUG = env_bool("DJANGO_DEBUG", False)
ALLOWED_HOSTS = [h.strip() for h in os.environ.get("ALLOWED_HOSTS", "*").split(",") if h.strip()]
CSRF_TRUSTED_ORIGINS = [
    o.strip()
    for o in os.environ.get("CSRF_TRUSTED_ORIGINS", "http://localhost:8080,http://127.0.0.1:8080").split(",")
    if o.strip()
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "whitenoise.runserver_nostatic",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "ninja",
    "audit",
    "accounts",
    "events",
    "judging",
    "community",
    "integrations",
    "records",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "audit.middleware.RequestActorMiddleware",
    "plumbline.middleware.OutOfRangeMiddleware",
]

ROOT_URLCONF = "plumbline.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "events.context_processors.site",
            ],
        },
    },
]

WSGI_APPLICATION = "plumbline.wsgi.application"

DATABASES = {
    "default": dj_database_url.config(
        default=f"sqlite:///{BASE_DIR / 'plumbline.sqlite3'}",
        conn_max_age=60,
        conn_health_checks=True,
    )
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Rate-limit counters live in the database so every gunicorn worker sees the
# same numbers. Created by `manage.py createcachetable` in the entrypoint.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.db.DatabaseCache",
        "LOCATION": "plumbline_cache",
        "TIMEOUT": 300,
        "OPTIONS": {"MAX_ENTRIES": 10000},
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

AUTHENTICATION_BACKENDS = ["accounts.backends.EmailOrUsernameBackend"]

LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "home"

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}

SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"

# Seeding. PLUMBLINE_SEED_SECRET makes the seeded API tokens reproducible so a
# committed .dogfood.toml keeps working across `docker compose down && up`.
PLUMBLINE_SEED_SECRET = os.environ.get("PLUMBLINE_SEED_SECRET", DEFAULT_SEED_SECRET)
PLUMBLINE_SEEDED = env_bool("PLUMBLINE_SEED", False)
# True while the installation still runs on the values printed in the README.
# Fine for trying the portal on your own machine; not for a real event.
PLUMBLINE_SAMPLE_SECRETS = SECRET_KEY == DEFAULT_SECRET_KEY or (
    PLUMBLINE_SEEDED and PLUMBLINE_SEED_SECRET == DEFAULT_SEED_SECRET
)
PLUMBLINE_SITE_NAME = os.environ.get("PLUMBLINE_SITE_NAME", "Plumbline")
# The public address, with scheme, no trailing slash. Links the portal prints
# for other people (certificates, sign-in links, ballot links, embeds) are
# built from it. Empty means "whatever address the request came to", which
# is right on a laptop and wrong behind anything that lets Host through.
PLUMBLINE_SITE_URL = os.environ.get("PLUMBLINE_SITE_URL", "").strip()

# Webhooks. Deliveries run in a background thread after the transaction
# commits, so a slow receiver never slows a judge down. Set ASYNC to 0 to
# deliver inline (used by the tests).
PLUMBLINE_WEBHOOKS_ASYNC = env_bool("PLUMBLINE_WEBHOOKS_ASYNC", True)
PLUMBLINE_WEBHOOK_TIMEOUT = float(os.environ.get("PLUMBLINE_WEBHOOK_TIMEOUT", "5"))
PLUMBLINE_WEBHOOK_MAX_ATTEMPTS = int(os.environ.get("PLUMBLINE_WEBHOOK_MAX_ATTEMPTS", "5"))

# Webhook receivers on loopback, private or link-local addresses are refused
# unless this is on: otherwise an organizer could make the portal call
# services that only the server can reach.
PLUMBLINE_WEBHOOK_ALLOW_PRIVATE = env_bool("PLUMBLINE_WEBHOOK_ALLOW_PRIVATE", False)

# Believe X-Forwarded-For only when a reverse proxy you control sets it.
PLUMBLINE_TRUST_PROXY = env_bool("PLUMBLINE_TRUST_PROXY", False)

# Rate limits for anonymous write endpoints (voting, comments). Requests per minute per client.
PLUMBLINE_ANON_WRITE_RATE = int(os.environ.get("PLUMBLINE_ANON_WRITE_RATE", "20"))

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}
