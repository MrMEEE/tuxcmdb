import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# Populated by the optional tuxcmdb-webui-agents package with installer
# artifacts (RPM/DEB/PowerShell script) for download from /agents/.
AGENTS_DIR = Path(os.environ.get("TUXCMDB_AGENTS_DIR", "/opt/tuxcmdb-webui-agents"))
SECRET_KEY = "tuxcmdb-webui-dev-secret-key-change-me"
DEBUG = True
ALLOWED_HOSTS = ["*"]

# Trust X-Forwarded-Proto from nginx/apache so request.is_secure() and
# build_absolute_uri() return the correct scheme behind a reverse proxy.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = True

# Origins allowed to submit unsafe (POST/PUT/...) requests, e.g. the login
# form. Required when the site is reached through a reverse proxy on a
# hostname/port that differs from what Django sees directly. Set via
# TUXCMDB_CSRF_TRUSTED_ORIGINS as a comma-separated list of full origins,
# for example: https://tuxcmdb.example.com,https://localhost:4443
CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("TUXCMDB_CSRF_TRUSTED_ORIGINS", "").split(",")
    if origin.strip()
]

INSTALLED_APPS = [
    "daphne",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "channels",
    "webui",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "webui.auth.SessionUserMiddleware",
]

ROOT_URLCONF = "tuxcmdb_webui.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    }
]

WSGI_APPLICATION = "tuxcmdb_webui.wsgi.application"
ASGI_APPLICATION = "tuxcmdb_webui.asgi.application"

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels.layers.InMemoryChannelLayer",
    }
}

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "webui-session.sqlite3",
    }
}

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATICFILES_DIRS = []
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
SESSION_ENGINE = "django.contrib.sessions.backends.db"
CSRF_COOKIE_HTTPONLY = False
