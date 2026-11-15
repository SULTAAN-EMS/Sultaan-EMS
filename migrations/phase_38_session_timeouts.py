"""Set the administrator idle timeout to 15 minutes."""

import argparse
import os
import sys

from sqlalchemy import text

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import create_app, db


VERSION = "phase_38_session_timeouts_v1"


def upgrade():
    app = create_app()
    with app.app_context():
        with db.engine.begin() as connection:
            if connection.dialect.name == "postgresql":
                connection.execute(text(
                    "SELECT pg_advisory_xact_lock(hashtext(:lock_name)::bigint)"
                ), {"lock_name": VERSION})
            connection.execute(text(
                "CREATE TABLE IF NOT EXISTS schema_migrations "
                "(version VARCHAR(120) PRIMARY KEY, applied_at TIMESTAMP NOT NULL)"
            ))
            if connection.dialect.name == "sqlite":
                connection.execute(text(
                    "INSERT INTO settings (key, value, created_at, updated_at) VALUES "
                    "('admin_session_timeout_minutes', '15', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP"
                ))
            elif connection.dialect.name == "mysql":
                connection.execute(text(
                    "INSERT INTO settings (`key`, `value`, created_at, updated_at) VALUES "
                    "('admin_session_timeout_minutes', '15', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP) "
                    "ON DUPLICATE KEY UPDATE `value` = VALUES(`value`), updated_at = CURRENT_TIMESTAMP"
                ))
            else:
                connection.execute(text(
                    "INSERT INTO settings (key, value, created_at, updated_at) VALUES "
                    "('admin_session_timeout_minutes', '15', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP) "
                    "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = CURRENT_TIMESTAMP"
                ))
            if connection.dialect.name == "sqlite":
                statement = (
                    "INSERT OR IGNORE INTO schema_migrations (version, applied_at) "
                    "VALUES (:version, CURRENT_TIMESTAMP)"
                )
            else:
                statement = (
                    "INSERT INTO schema_migrations (version, applied_at) "
                    "VALUES (:version, CURRENT_TIMESTAMP) ON CONFLICT (version) DO NOTHING"
                )
            connection.execute(text(statement), {"version": VERSION})
    return {"status": "applied", "version": VERSION}


def main():
    argparse.ArgumentParser(description="Set administrator and teacher session timeouts").parse_args()
    result = upgrade()
    print(f"Applied {result['version']}")


if __name__ == "__main__":
    main()
