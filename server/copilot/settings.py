import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "insecure-dev-key")
DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"
ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "*").split(",")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "channels",
    "audio",  # Phase 0/1: ping test + audio ingestion consumer
    "conversation",  # Phase 1: Session/Turn models (Phase 2+ will add manager/state/intent/memory here)
    "api",  # REST endpoints: session management, history
    "ai",  # Phase 3/5: LLM suggestion generation + answer-format classification
           # (was missing from INSTALLED_APPS despite the app existing since
           # Phase 3 — found here in Phase B, 2026-08-23, while adding
           # score_answer_format as a management command: Django won't
           # discover an app's management commands unless the app is
           # actually registered, so this was a real bug, not just missing
           # for score_answer_format's sake)
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "copilot.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# ASGI app (Channels) instead of WSGI — required for WebSocket support
ASGI_APPLICATION = "copilot.asgi.application"

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {
            "hosts": [os.environ.get("REDIS_URL", "redis://localhost:6379/0")],
        },
    },
}

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        # Configurable so Docker deployments can point this at a dedicated
        # volume (SQLITE_DB_PATH=/data/db.sqlite3, set in the compose
        # files) instead of the default below. That default previously
        # resolved to BASE_DIR/db.sqlite3 — server/db.sqlite3 — which is
        # INSIDE the bind-mounted, git-tracked source tree (../server:/app
        # in both compose files). That meant the live database was really
        # just a plain file in the working directory: a `git pull` on the
        # host silently overwrote it under a running container mid-session
        # (no crash, no restart — the exact "table just vanished hours in"
        # bug from 2026-08-12 testing). Moving it to its own volume makes
        # that structurally impossible instead of relying on remembering
        # not to touch it.
        "NAME": os.environ.get("SQLITE_DB_PATH", str(BASE_DIR / "db.sqlite3")),
    }
}

AUTH_PASSWORD_VALIDATORS = []

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
