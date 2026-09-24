"""Add nullable soft-delete fields to Behavior Attendance records.

Existing records remain active/voided and are not rewritten. Deleted rows are
kept as read-only evidence and excluded by the application from scoring and
operational views.
"""

import argparse
import os
import sys

from sqlalchemy import inspect, text

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import create_app, db


VERSION = "phase_2e_behavior_attendance_soft_delete_v1"


def _record_migration(connection):
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


def upgrade():
    app = create_app()
    with app.app_context():
        with db.engine.begin() as connection:
            columns = {item["name"] for item in inspect(connection).get_columns("behavior_attendance_records")}
            additions = {
                "deleted_by": "INTEGER",
                "deleted_at": "TIMESTAMP",
                "deletion_reason": "VARCHAR(255)",
            }
            for name, column_type in additions.items():
                if name not in columns:
                    connection.execute(text(
                        f"ALTER TABLE behavior_attendance_records ADD COLUMN {name} {column_type}"
                    ))
            indexes = {item["name"] for item in inspect(connection).get_indexes("behavior_attendance_records")}
            if "ix_behavior_attendance_records_deleted_by" not in indexes:
                connection.execute(text(
                    "CREATE INDEX ix_behavior_attendance_records_deleted_by "
                    "ON behavior_attendance_records (deleted_by)"
                ))
            if "ix_behavior_attendance_records_deleted_at" not in indexes:
                connection.execute(text(
                    "CREATE INDEX ix_behavior_attendance_records_deleted_at "
                    "ON behavior_attendance_records (deleted_at)"
                ))
            _record_migration(connection)


def main():
    argparse.ArgumentParser(description="Add soft-delete fields to Behavior Attendance records").parse_args()
    upgrade()
    print(f"Applied {VERSION}")


if __name__ == "__main__":
    main()
