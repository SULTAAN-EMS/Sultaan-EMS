"""Add per-student locked-result message fields."""

import argparse
import os
import sys

from sqlalchemy import inspect, text

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import create_app, db


VERSION = "phase_8_locked_result_messages_v1"


def upgrade():
    app = create_app()
    with app.app_context():
        inspector = inspect(db.engine)
        if not inspector.has_table("students"):
            raise RuntimeError("students table does not exist")

        columns = {column["name"] for column in inspector.get_columns("students")}
        dialect = db.engine.dialect.name
        timestamp_type = "TIMESTAMP" if dialect == "postgresql" else "DATETIME"

        with db.engine.begin() as connection:
            if "lock_admin_message" not in columns:
                connection.execute(text("ALTER TABLE students ADD COLUMN lock_admin_message TEXT"))
            if "lock_admin_message_at" not in columns:
                connection.execute(text(
                    f"ALTER TABLE students ADD COLUMN lock_admin_message_at {timestamp_type}"
                ))

            connection.execute(text(
                "CREATE TABLE IF NOT EXISTS schema_migrations "
                "(version VARCHAR(120) PRIMARY KEY, applied_at TIMESTAMP NOT NULL)"
            ))
            if dialect == "postgresql":
                connection.execute(text(
                    "INSERT INTO schema_migrations (version, applied_at) "
                    "VALUES (:version, CURRENT_TIMESTAMP) ON CONFLICT (version) DO NOTHING"
                ), {"version": VERSION})
            elif dialect == "sqlite":
                connection.execute(text(
                    "INSERT OR IGNORE INTO schema_migrations (version, applied_at) "
                    "VALUES (:version, CURRENT_TIMESTAMP)"
                ), {"version": VERSION})
            else:
                connection.execute(text(
                    "INSERT IGNORE INTO schema_migrations (version, applied_at) "
                    "VALUES (:version, CURRENT_TIMESTAMP)"
                ), {"version": VERSION})


def main():
    argparse.ArgumentParser(description="Add locked-result message fields").parse_args()
    upgrade()
    print(f"Applied {VERSION}; existing student records were preserved")


if __name__ == "__main__":
    main()
