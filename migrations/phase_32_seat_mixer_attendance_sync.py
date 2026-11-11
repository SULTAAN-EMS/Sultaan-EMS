"""Add explicit Seat Mixer snapshots to exam attendance hall rosters."""

import argparse
import os
import sys

from sqlalchemy import inspect, text

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import create_app, db


VERSION = "phase_32_seat_mixer_attendance_sync_v1"
COLUMNS = {
    "exam_halls": (
        "attendance_seat_plan_version_id",
        "attendance_seat_plan_snapshot_id",
    ),
    "exam_hall_enrollments": (
        "seat_plan_version_id",
        "seat_plan_snapshot_id",
        "seat_row_number",
        "seat_table_number",
        "seat_number",
    ),
}


def ensure_columns(connection):
    tables = set(inspect(connection).get_table_names())
    missing_tables = set(COLUMNS) - tables
    if missing_tables:
        raise RuntimeError(
            "Required attendance tables are missing: "
            + ", ".join(sorted(missing_tables))
        )

    for table, names in COLUMNS.items():
        existing = {
            column["name"]
            for column in inspect(connection).get_columns(table)
        }
        for name in names:
            if name not in existing:
                connection.execute(text(
                    f"ALTER TABLE {table} ADD COLUMN {name} INTEGER"
                ))
                existing.add(name)


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
            ensure_columns(connection)

            if connection.dialect.name == "sqlite":
                statement = (
                    "INSERT OR IGNORE INTO schema_migrations (version, applied_at) "
                    "VALUES (:version, CURRENT_TIMESTAMP)"
                )
            elif connection.dialect.name == "postgresql":
                statement = (
                    "INSERT INTO schema_migrations (version, applied_at) "
                    "VALUES (:version, CURRENT_TIMESTAMP) ON CONFLICT (version) DO NOTHING"
                )
            else:
                statement = (
                    "INSERT IGNORE INTO schema_migrations (version, applied_at) "
                    "VALUES (:version, CURRENT_TIMESTAMP)"
                )
            connection.execute(text(statement), {"version": VERSION})

    return {"status": "applied", "version": VERSION, "columns": COLUMNS}


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    upgrade()
    print(f"Applied {VERSION}")


if __name__ == "__main__":
    main()
