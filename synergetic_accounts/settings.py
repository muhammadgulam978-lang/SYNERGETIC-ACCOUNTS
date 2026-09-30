import os
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlparse

BASE_DIR = Path(__file__).resolve().parent.parent


def _split_csv_env(var_name: str, default: str) -> list[str]:
    return [value.strip() for value in os.environ.get(var_name, default).split(",") if value.strip()]


def _env_bool(var_name: str, default: bool = False) -> bool:
    value = os.environ.get(var_name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _normalize_allowed_hosts(hosts: list[str]) -> list[str]:
    normalized: list[str] = []
    for host in hosts:
        if not host:
            continue
        if host.startswith("*."):
            normalized.append(f".{host[2:]}")
        elif host.startswith("."):
            normalized.append(host)
        else:
            normalized.append(host)
    return normalized


def _allowed_hosts() -> list[str]:
    hosts = _split_csv_env(
        "SYNERGETIC_ALLOWED_HOSTS",
        "127.0.0.1,localhost,.vercel.app"
    )
    return _normalize_allowed_hosts(hosts)


def _first_database_url() -> str:
    """Return the first managed-Postgres URL exposed by common Vercel integrations."""
    for var_name in (
        "DATABASE_URL",
        "POSTGRES_URL",
        "POSTGRES_PRISMA_URL",
        "POSTGRES_URL_NON_POOLING",
    ):
        value = os.environ.get(var_name, "").strip()
        if value:
            return value
    return ""


def _postgres_config_from_url(database_url: str) -> dict:
    parsed = urlparse(database_url)
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise ValueError("The database URL must use the postgres or postgresql scheme.")

    options = dict(parse_qsl(parsed.query, keep_blank_values=False))
    hostname = parsed.hostname or ""
    if hostname not in {"", "127.0.0.1", "localhost"}:
        options.setdefault("sslmode", "require")

    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": unquote(parsed.path.lstrip("/")),
        "USER": unquote(parsed.username or ""),
        "PASSWORD": unquote(parsed.password or ""),
        "HOST": hostname,
        "PORT": str(parsed.port or 5432),
        "CONN_MAX_AGE": int(os.environ.get("SYNERGETIC_DB_CONN_MAX_AGE", "0" if _env_bool("VERCEL") else "60")),
        "CONN_HEALTH_CHECKS": True,
        "OPTIONS": options,
    }


SECRET_KEY = os.environ.get("SYNERGETIC_SECRET_KEY", "local-development-key-change-before-production")
DEBUG = _env_bool("SYNERGETIC_DEBUG", default=not _env_bool("VERCEL"))

ALLOWED_HOSTS = _allowed_hosts()
CSRF_TRUSTED_ORIGINS = _split_csv_env(
    "SYNERGETIC_CSRF_TRUSTED_ORIGINS",
    "https://localhost,https://127.0.0.1,https://*.vercel.app"
)
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = True
SECURE_SSL_REDIRECT = not DEBUG
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SECURE_HSTS_SECONDS = 31536000 if not DEBUG else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = not DEBUG
SECURE_HSTS_PRELOAD = not DEBUG

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

DATABASE_URL = _first_database_url()
if DATABASE_URL:
    DATABASES = {"default": _postgres_config_from_url(DATABASE_URL)}
elif not _env_bool("SYNERGETIC_USE_SQLITE", default=False):
    DATABASES = {"default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("SYNERGETIC_DB_NAME", "synergetic_accounts"),
        "USER": os.environ.get("SYNERGETIC_DB_USER", "synergetic_app"),
        "PASSWORD": os.environ.get("SYNERGETIC_DB_PASSWORD", ""),
        "HOST": os.environ.get("SYNERGETIC_DB_HOST", "127.0.0.1"),
        "PORT": os.environ.get("SYNERGETIC_DB_PORT", "5432"),
        "CONN_MAX_AGE": int(os.environ.get("SYNERGETIC_DB_CONN_MAX_AGE", "0" if _env_bool("VERCEL") else "60")),
        "CONN_HEALTH_CHECKS": True,
    }}
else:
    DATABASES = {"default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
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
