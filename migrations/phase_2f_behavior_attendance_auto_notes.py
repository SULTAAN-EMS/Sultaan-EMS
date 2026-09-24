"""Add explicit system-generated note state to Behavior Attendance records."""

import argparse
import os
import sys

from sqlalchemy import inspect, text

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import create_app, db


VERSION = "phase_2f_behavior_attendance_auto_notes_v1"


def upgrade():
    app = create_app()
    with app.app_context():
        with db.engine.begin() as connection:
            tables = set(inspect(connection).get_table_names())
            if "behavior_attendance_records" in tables:
                columns = {
                    item["name"]
                    for item in inspect(connection).get_columns("behavior_attendance_records")
                }
                if "note_is_auto_generated" not in columns:
                    connection.execute(text(
                        "ALTER TABLE behavior_attendance_records "
                        "ADD COLUMN note_is_auto_generated BOOLEAN NOT NULL DEFAULT FALSE"
                    ))
            connection.execute(text(
                "CREATE TABLE IF NOT EXISTS schema_migrations "
                "(version VARCHAR(120) PRIMARY KEY, applied_at TIMESTAMP NOT NULL)"
            ))
            if connection.dialect.name == "sqlite":
                connection.execute(text(
                    "INSERT OR IGNORE INTO schema_migrations "
                    "(version, applied_at) VALUES (:version, CURRENT_TIMESTAMP)"
                ), {"version": VERSION})
            elif connection.dialect.name == "mysql":
                connection.execute(text(
                    "INSERT IGNORE INTO schema_migrations "
                    "(version, applied_at) VALUES (:version, CURRENT_TIMESTAMP)"
                ), {"version": VERSION})
            else:
                connection.execute(text(
                    "INSERT INTO schema_migrations (version, applied_at) "
                    "VALUES (:version, CURRENT_TIMESTAMP) ON CONFLICT (version) DO NOTHING"
                ), {"version": VERSION})


def main():
    argparse.ArgumentParser(description="Add Behavior Attendance auto-note state").parse_args()
    upgrade()
    print(f"Applied {VERSION}")


if __name__ == "__main__":
    main()
