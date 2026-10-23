"""Django settings, read from environment variables (and `.env` in development).

Every value that differs between environments comes from the environment, so
the same Docker image runs locally, in CI and in production.
"""

from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env()
environ.Env.read_env(BASE_DIR / ".env")

# --- Core -----------------------------------------------------------------

SECRET_KEY = env.str("DJANGO_SECRET_KEY")
DEBUG = env.bool("DJANGO_DEBUG", default=False)
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "core",
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
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# --- Database ---------------------------------------------------------------

DATABASES = {
    "default": env.db(
        "DATABASE_URL",
        default="postgres://flaky:flaky@localhost:5432/flaky_tracker",
    ),
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Static files (admin CSS/JS, served by WhiteNoise) -----------------------

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}
# In development, serve static files from the apps directly (no collectstatic needed).
WHITENOISE_USE_FINDERS = DEBUG
WHITENOISE_AUTOREFRESH = DEBUG

# --- Security (production) ----------------------------------------------------
# The load balancer terminates HTTPS and forwards plain HTTP with this header.

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = env.bool("DJANGO_SECURE_SSL_REDIRECT", default=not DEBUG)
SECURE_REDIRECT_EXEMPT = [r"^healthz$"]  # load balancer health checks use plain HTTP
SECURE_HSTS_SECONDS = env.int("DJANGO_SECURE_HSTS_SECONDS", default=0 if DEBUG else 3600)
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG

# --- Internationalization ---------------------------------------------------

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# --- Celery (background jobs) -------------------------------------------------

CELERY_BROKER_URL = env.str("REDIS_URL", default="redis://localhost:6379/0")
CELERY_TASK_ACKS_LATE = True  # re-deliver a task if the worker dies mid-task
CELERY_WORKER_PREFETCH_MULTIPLIER = 1  # take one task at a time
CELERY_TASK_TIME_LIMIT = 300

# --- GitHub App (empty until the App is registered in Week 0) -----------------

GITHUB_APP_CLIENT_ID = env.str("GITHUB_APP_CLIENT_ID", default="")
GITHUB_APP_PRIVATE_KEY_PATH = env.path(
    "GITHUB_APP_PRIVATE_KEY_PATH", default=BASE_DIR / "github-app.private-key.pem"
)
GITHUB_WEBHOOK_SECRET = env.str("GITHUB_WEBHOOK_SECRET", default="")

# --- LLM explanations (skipped when no key is set) ----------------------------

LLM_API_KEY = env.str("LLM_API_KEY", default="")
LLM_MODEL = env.str("LLM_MODEL", default="")

# --- Flaky detection ----------------------------------------------------------

FLAKY_WINDOW_DAYS = env.int("FLAKY_WINDOW_DAYS", default=30)
FLAKY_MIN_EVENTS = env.int("FLAKY_MIN_EVENTS", default=1)

# --- Logging ------------------------------------------------------------------

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": env.str("LOG_LEVEL", default="INFO")},
}
