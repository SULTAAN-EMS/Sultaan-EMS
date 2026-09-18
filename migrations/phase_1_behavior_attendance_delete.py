"""Add read-only tombstones for hard-deleted Behavior Attendance records.

The original Attendance row is physically removed. This separate snapshot is
not an Attendance record and is never included by scoring, reports, portal
views, or operational history.
"""

import argparse
import os
import sys

from sqlalchemy import inspect, text

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import create_app, db


VERSION = "phase_1_behavior_attendance_delete_v1"


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
            tables = inspect(connection).get_table_names()
            if "behavior_attendance_deletions" not in tables:
                id_sql = (
                    "SERIAL PRIMARY KEY"
                    if connection.dialect.name == "postgresql"
                    else "INTEGER PRIMARY KEY"
                )
                connection.execute(text(
                    "CREATE TABLE behavior_attendance_deletions ("
                    f"id {id_sql}, "
                    "original_record_id INTEGER NOT NULL, "
                    "student_id INTEGER, student_enrollment_id INTEGER, "
                    "behavior_configuration_id INTEGER, behavior_session_id INTEGER, "
                    "academic_year_id INTEGER, academic_year_level_id INTEGER, "
                    "academic_year_class_id INTEGER, "
                    "student_name VARCHAR(180) NOT NULL, student_code VARCHAR(80) NOT NULL, "
                    "mother_name VARCHAR(180), class_name VARCHAR(120), session_label VARCHAR(120), "
                    "attendance_date DATE NOT NULL, attendance_time TIME, arrival_time TIME, "
                    "late_by_minutes INTEGER, status_key VARCHAR(40) NOT NULL, "
                    "status_label VARCHAR(120) NOT NULL, original_status VARCHAR(10) NOT NULL, "
                    "polarity VARCHAR(10) NOT NULL, points_applied NUMERIC(8,3) NOT NULL DEFAULT 0, "
                    "note VARCHAR(255), void_reason VARCHAR(255), deletion_reason VARCHAR(255) NOT NULL, "
                    "deleted_by_username VARCHAR(80) NOT NULL, "
                    "deleted_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP"
                    ")"
                ))
            indexes = {item["name"] for item in inspect(connection).get_indexes("behavior_attendance_deletions")}
            for name, columns in {
                "ix_behavior_attendance_deletions_original_record_id": ["original_record_id"],
                "ix_behavior_attendance_deletions_scope": ["behavior_configuration_id", "behavior_session_id"],
                "ix_behavior_attendance_deletions_student_enrollment_id": ["student_enrollment_id"],
                "ix_behavior_attendance_deletions_attendance_date": ["attendance_date"],
                "ix_behavior_attendance_deletions_deleted_at": ["deleted_at"],
            }.items():
                if name not in indexes:
                    connection.execute(text(
                        f"CREATE INDEX {name} ON behavior_attendance_deletions ({', '.join(columns)})"
                    ))
            _record_migration(connection)


def main():
    argparse.ArgumentParser(description="Add Attendance hard-delete tombstones").parse_args()
    upgrade()
    print(f"Applied {VERSION}")


if __name__ == "__main__":
    main()
