"""Add the additive, auditable VOID lifecycle to Behavior Attendance rows.

This migration never deletes or rewrites an Attendance record. Existing rows
are ACTIVE by default; only a later explicit administrative action can mark a
row VOIDED.
"""

import argparse
import os
import sys

from sqlalchemy import inspect, text

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import create_app, db


VERSION = "phase_1_behavior_attendance_void_v1"


def _add_column(connection, table, name, definition):
    if table not in inspect(connection).get_table_names():
        return
    columns = {item["name"] for item in inspect(connection).get_columns(table)}
    if name not in columns:
        connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {definition}"))


def _add_index(connection, table, name, columns):
    if table not in inspect(connection).get_table_names():
        return
    indexes = {item["name"] for item in inspect(connection).get_indexes(table)}
    if name not in indexes:
        column_sql = ", ".join(columns)
        connection.execute(text(f"CREATE INDEX {name} ON {table} ({column_sql})"))


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
            datetime_type = "TIMESTAMP" if connection.dialect.name == "postgresql" else "DATETIME"
            has_attendance_table = "behavior_attendance_records" in inspect(connection).get_table_names()
            _add_column(connection, "behavior_attendance_records", "status", "VARCHAR(10) NOT NULL DEFAULT 'active'")
            _add_column(connection, "behavior_attendance_records", "voided_by", "INTEGER")
            _add_column(connection, "behavior_attendance_records", "voided_at", datetime_type)
            _add_column(connection, "behavior_attendance_records", "void_reason", "VARCHAR(255)")
            if has_attendance_table:
                connection.execute(text(
                    "UPDATE behavior_attendance_records SET status = 'active' "
                    "WHERE status IS NULL"
                ))
            _add_index(connection, "behavior_attendance_records", "ix_behavior_attendance_records_status", ["status"])
            _add_index(connection, "behavior_attendance_records", "ix_behavior_attendance_records_voided_by", ["voided_by"])
            _record_migration(connection)


def main():
    argparse.ArgumentParser(description="Add auditable Attendance VOID lifecycle").parse_args()
    upgrade()
    print(f"Applied {VERSION}")


if __name__ == "__main__":
    main()
