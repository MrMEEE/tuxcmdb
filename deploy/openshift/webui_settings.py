"""Production settings for the single-replica OpenShift WebUI."""

import os

from tuxcmdb_webui.settings import *  # noqa: F403

SECRET_KEY = os.environ["TUXCMDB_WEBUI_SECRET_KEY"]
DEBUG = False
INSTALLATION_MODE = "openshift"
AGENT_INSTALL_DATA_DIR = Path("/opt/app-root/src/webui-data/agent-installs")  # noqa: F405
ALLOWED_HOSTS = [os.environ["TUXCMDB_WEBUI_HOST"]]
CSRF_TRUSTED_ORIGINS = [f"https://{os.environ['TUXCMDB_WEBUI_HOST']}"]
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": "/opt/app-root/src/webui-data/sessions.sqlite3",
    }
}
STATIC_ROOT = "/opt/app-root/src/staticfiles"
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    *MIDDLEWARE[1:],  # noqa: F405
]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}
