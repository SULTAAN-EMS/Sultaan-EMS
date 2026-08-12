import os
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent

# Local dev only (Render ignores .env anyway)
load_dotenv(BASE_DIR / ".env")


def _get_database_uri():
    raw_url = os.getenv("DATABASE_URL", "").strip()
    if (raw_url.startswith('"') and raw_url.endswith('"')) or (raw_url.startswith("'") and raw_url.endswith("'")):
        raw_url = raw_url[1:-1].strip()

    if raw_url:
        if raw_url.startswith("postgres://"):
            raw_url = raw_url.replace("postgres://", "postgresql://", 1)

        if raw_url.startswith("postgresql://") and not raw_url.startswith("postgresql+"):
            try:
                import psycopg  # noqa: F401
                try:
                    import psycopg2  # noqa: F401
                except ImportError:
                    raw_url = raw_url.replace("postgresql://", "postgresql+psycopg://", 1)
            except ImportError:
                pass
        elif raw_url.startswith("mysql://"):
            raw_url = raw_url.replace("mysql://", "mysql+pymysql://", 1)

        return raw_url

    is_production = any([
        os.getenv("RENDER"),
        os.getenv("RAILWAY_ENVIRONMENT"),
        os.getenv("RAILWAY_SERVICE_ID"),
        os.getenv("FLASK_ENV") == "production",
        os.getenv("ENVIRONMENT") == "production",
        os.getenv("ENV") == "production",
    ])

    if is_production:
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
