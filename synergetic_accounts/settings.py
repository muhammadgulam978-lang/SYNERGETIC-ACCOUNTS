import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _split_csv_env(var_name: str, default: str) -> list[str]:
    return [value.strip() for value in os.environ.get(var_name, default).split(",") if value.strip()]


SECRET_KEY = os.environ.get("SYNERGETIC_SECRET_KEY", "local-development-key-change-before-production")
DEBUG = os.environ.get("SYNERGETIC_DEBUG", "1") == "1"
ALLOWED_HOSTS = _split_csv_env("SYNERGETIC_ALLOWED_HOSTS", "127.0.0.1,localhost,*.vercel.app")
CSRF_TRUSTED_ORIGINS = _split_csv_env("SYNERGETIC_CSRF_TRUSTED_ORIGINS", "https://localhost,https://127.0.0.1,https://*.vercel.app")
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "ledger",
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

ROOT_URLCONF = "synergetic_accounts.urls"
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [BASE_DIR / "templates"],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
    ]},
}]
WSGI_APPLICATION = "synergetic_accounts.wsgi.application"

DATABASES = {"default": {
    "ENGINE": "django.db.backends.postgresql",
    "NAME": os.environ.get("SYNERGETIC_DB_NAME", "synergetic_accounts"),
    "USER": os.environ.get("SYNERGETIC_DB_USER", "synergetic_app"),
    "PASSWORD": os.environ.get("SYNERGETIC_DB_PASSWORD", ""),
    "HOST": os.environ.get("SYNERGETIC_DB_HOST", "127.0.0.1"),
    "PORT": os.environ.get("SYNERGETIC_DB_PORT", "5432"),
    "CONN_MAX_AGE": 60,
}}
AUTH_PASSWORD_VALIDATORS = []
LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Karachi"
USE_I18N = True
USE_TZ = True
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "ledger-dashboard"
LOGOUT_REDIRECT_URL = "login"

FILE_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = 12 * 1024 * 1024
