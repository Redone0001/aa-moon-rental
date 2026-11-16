"""Real Alliance Auth integration settings. SQLite is for tests and local screenshots only."""

import os
from pathlib import Path
from unittest.mock import Mock

from allianceauth.project_template.project_name.settings.base import *  # noqa: F403
from allianceauth.utils import cache as aa_cache

# Mock infrastructure only in this isolated test/demo process. Application models,
# permissions, migrations, templates, moonmining and structures adapters remain real.
_redis = Mock()
_redis.ping.return_value = False
_redis.info.return_value = {"redis_version": "7.4.0"}
aa_cache.get_redis_client = lambda: _redis

BASE_DIR = Path(__file__).resolve().parent.parent
SECRET_KEY = "moon-rental-tests-only-not-for-production"
DEBUG = True
SITE_NAME = "Alliance Auth"
SITE_URL = "http://localhost:8000"
CSRF_TRUSTED_ORIGINS = ["http://localhost:8000", "http://127.0.0.1:8000"]
DEFAULT_THEME = "allianceauth.theme.darkly.auth_hooks.DarklyThemeHook"
ALLOWED_HOSTS = ["testserver", "localhost", "127.0.0.1"]
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "demo.sqlite3"}}
if os.environ.get("MOON_RENTAL_TEST_MYSQL"):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.mysql",
            "NAME": "moon_rental",
            "USER": "root",
            "PASSWORD": "test",
            "HOST": "127.0.0.1",
            "PORT": "3306",
        }
    }
INSTALLED_APPS = [*INSTALLED_APPS, "eveuniverse", "moonmining", "structures", "moon_rental"]  # noqa: F405
ROOT_URLCONF = "tests.urls"
WSGI_APPLICATION = None
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
AUTHENTICATION_BACKENDS = ["django.contrib.auth.backends.ModelBackend"]
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
MEDIA_URL = "/media/"
TIME_ZONE = "UTC"
USE_TZ = True
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"null": {"class": "logging.NullHandler"}},
    "root": {"handlers": ["null"]},
}
CELERY_TASK_ALWAYS_EAGER = True
CELERY_BROKER_URL = "memory://"
CELERY_RESULT_BACKEND = "cache+memory://"
CELERYBEAT_SCHEDULE = {}
ESI_SSO_CLIENT_ID = "test-client-id"
ESI_SSO_CLIENT_SECRET = "test-client-secret"
ESI_SSO_CALLBACK_URL = "http://localhost:8000/sso/callback"
ESI_USER_CONTACT_EMAIL = "tests@example.invalid"
ESI_USER_AGENT = "aa-moon-rental-tests"
ESI_API_SCHEMA_CACHE_DIR = "/tmp/moon-rental-esi-cache"
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
DEFAULT_AUTO_FIELD = "django.db.models.AutoField"
