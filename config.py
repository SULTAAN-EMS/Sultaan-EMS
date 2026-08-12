import os
import logging
import sys
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
logger = logging.getLogger(__name__)

def _is_production_environment():
    """Return True only for an explicitly production-like process."""
    explicit = (os.getenv("APP_ENV") or os.getenv("ENVIRONMENT") or os.getenv("FLASK_ENV") or os.getenv("ENV") or "").strip().lower()
    if explicit in {"production", "prod"}:
        return True
    # Render/Railway set service identifiers in deployed processes. These are
    # only deployment markers; DATABASE_URL itself remains the source of truth.
    # `RENDER_SERVICE_ID` is Render's stable service-level marker. The shorter
    # `RENDER` flag is not guaranteed to be present on every Render runtime.
    if any(os.getenv(marker) for marker in (
        "RENDER",
        "RENDER_SERVICE_ID",
        "RENDER_SERVICE_NAME",
        "RENDER_EXTERNAL_URL",
        "RAILWAY_ENVIRONMENT",
        "RAILWAY_SERVICE_ID",
    )):
        return True
    # Render's service does not guarantee a RENDER marker. Gunicorn is the
    # production WSGI entrypoint in this project, while local development is
    # started through `python run.py` or the Flask CLI.
    executable = Path(sys.argv[0]).name.lower()
    return executable.startswith("gunicorn")


# `.env` is strictly a local-development convenience. A production process
# must never obtain its database configuration from a checked-out local file.
if not _is_production_environment():
    load_dotenv(BASE_DIR / ".env")


def _normalise_database_url(raw_url):
    """Convert supported provider URLs to installed SQLAlchemy drivers."""
    raw_url = (raw_url or "").strip()
    if (raw_url.startswith('"') and raw_url.endswith('"')) or (raw_url.startswith("'") and raw_url.endswith("'")):
        raw_url = raw_url[1:-1].strip()

    if raw_url.startswith("postgres://"):
        return raw_url.replace("postgres://", "postgresql+psycopg://", 1)
    if raw_url.startswith("postgresql://"):
        return raw_url.replace("postgresql://", "postgresql+psycopg://", 1)
    if raw_url.startswith("mysql://"):
        return raw_url.replace("mysql://", "mysql+pymysql://", 1)
    return raw_url


def _safe_database_diagnostic(raw_url, normalised_url):
    """Log database configuration without ever logging credentials."""
    if not raw_url:
        message = "DATABASE_URL detected: no; database configuration is absent"
        logger.error(message)
        # Config is imported before Flask/Gunicorn configures application
        # logging, so also write this safe, credential-free diagnostic to
        # stderr for the provider's startup log.
        print(message, file=sys.stderr, flush=True)
        return
    parsed = urlsplit(normalised_url)
    scheme = parsed.scheme or "unknown"
    dialect = scheme.split("+", 1)[0]
    message = "DATABASE_URL detected: yes; scheme: %s; host: configured; password: hidden" % dialect
    logger.info(message)
    # Do not print parsed host/user/query values. The message intentionally
    # confirms only presence and the selected SQLAlchemy dialect.
    print(message, file=sys.stderr, flush=True)


def _get_database_uri():
    # Read the current process environment after dotenv has loaded local-only
    # values. Render/Railway values win because load_dotenv does not override.
    raw_url = os.getenv("DATABASE_URL", "")
    database_url = _normalise_database_url(raw_url)
    _safe_database_diagnostic(raw_url.strip(), database_url)
    if database_url:
        return database_url

    if _is_production_environment():
        raise RuntimeError(
            "CRITICAL CONFIGURATION ERROR: DATABASE_URL environment variable is missing in production. "
            "Production deployments (Render / Railway) require a valid PostgreSQL or MySQL DATABASE_URL. "
            "Fallback to local SQLite is disabled in production to prevent read-only filesystem errors."
        )

    instance_dir = BASE_DIR / "instance"
    instance_dir.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{instance_dir / 'visual_review.db'}"


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-change-me")

    SQLALCHEMY_DATABASE_URI = _get_database_uri()

    SQLALCHEMY_TRACK_MODIFICATIONS = False
    
    # Default SQLite / MySQL / PostgreSQL options for production (Railway + Render)
    if SQLALCHEMY_DATABASE_URI.startswith("sqlite"):
        SQLALCHEMY_ENGINE_OPTIONS = {
            "pool_pre_ping": True,
            "pool_recycle": 1800,
        }
    elif SQLALCHEMY_DATABASE_URI.startswith("mysql"):
        # MySQL options for production (Render + Railway)
        SQLALCHEMY_ENGINE_OPTIONS = {
            "pool_pre_ping": True,
            "pool_recycle": 600,  # Recycle connections every 10 minutes to handle Railway MySQL timeouts
            "pool_use_lifo": True,
            "pool_size": 5,
            "max_overflow": 10,
            "pool_timeout": 30,
            "pool_reset_on_return": "rollback",
            "connect_args": {
                "connect_timeout": 10,
                "read_timeout": 30,
                "write_timeout": 30,
                "charset": "utf8mb4",
            },
        }
    else:
        # PostgreSQL / default options
        SQLALCHEMY_ENGINE_OPTIONS = {
            "pool_pre_ping": True,
            "pool_recycle": 600,
            "pool_use_lifo": True,
            "pool_size": 5,
            "max_overflow": 10,
            "pool_timeout": 30,
        }

    WTF_CSRF_TIME_LIMIT = None

    UPLOAD_FOLDER = str(
        BASE_DIR / os.getenv("UPLOAD_FOLDER", "app/static/uploads")
    )

    MAX_CONTENT_LENGTH = int(
        os.getenv("MAX_CONTENT_LENGTH", str(5 * 1024 * 1024))
    )

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_DURATION = timedelta(days=30)
    JSON_SORT_KEYS = False

    # Cloudinary
    CLOUDINARY_CLOUD_NAME = os.getenv("CLOUDINARY_CLOUD_NAME")
    CLOUDINARY_API_KEY = os.getenv("CLOUDINARY_API_KEY")
    CLOUDINARY_API_SECRET = os.getenv("CLOUDINARY_API_SECRET")
