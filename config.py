import os
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent

# Local dev only (Render ignores .env anyway)
load_dotenv(BASE_DIR / ".env")


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-change-me")

    # IMPORTANT: must come ONLY from environment, with SQLite fallback for local dev
    _db_url = os.environ.get(
        "DATABASE_URL",
        f"sqlite:///{BASE_DIR / 'instance' / 'visual_review.db'}"
    )
    if _db_url:
        if _db_url.startswith("postgres://"):
            _db_url = _db_url.replace("postgres://", "postgresql+psycopg://", 1)
        elif _db_url.startswith("postgresql://"):
            _db_url = _db_url.replace("postgresql://", "postgresql+psycopg://", 1)
        elif _db_url.startswith("mysql://"):
            _db_url = _db_url.replace("mysql://", "mysql+pymysql://", 1)

    SQLALCHEMY_DATABASE_URI = _db_url

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
