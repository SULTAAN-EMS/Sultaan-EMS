"""Add shift-scoped Attendance points and active-day calendars.

Existing settings are copied to both shifts as a non-destructive starting
point. Subsequent edits are isolated by year-level and school shift.
"""

import argparse
import os
import sys

from sqlalchemy import text

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import create_app, db
from app.models import (
    AcademicYearLevelShiftAttendanceDay,
    BehaviorAttendanceStatusShiftLevel,
)


VERSION = "phase_30_behavior_shift_attendance_settings_v1"
SHIFTS = ("morning", "afternoon")


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
    else:
        connection.execute(text(
            "INSERT INTO schema_migrations (version, applied_at) "
            "VALUES (:version, CURRENT_TIMESTAMP) ON CONFLICT (version) DO NOTHING"
        ), {"version": VERSION})


def _backfill(connection):
    level_days = connection.execute(text(
        "SELECT academic_year_level_id, weekday, label, is_active "
        "FROM academic_year_level_attendance_days"
    )).mappings().all()
    for day in level_days:
        for shift in SHIFTS:
            connection.execute(text(
                "INSERT INTO academic_year_level_shift_attendance_days "
                "(academic_year_level_id, school_shift, weekday, label, is_active, created_at, updated_at) "
                "VALUES (:level_id, :shift, :weekday, :label, :is_active, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP) "
                "ON CONFLICT (academic_year_level_id, school_shift, weekday) DO NOTHING"
            ), {
                "level_id": day["academic_year_level_id"],
                "shift": shift,
                "weekday": day["weekday"],
                "label": day["label"],
                "is_active": day["is_active"],
            })

    point_rows = connection.execute(text(
        "SELECT s.id AS status_id, s.behavior_configuration_id AS configuration_id, "
        "COALESCE(level_points.academic_year_level_id, config_levels.academic_year_level_id, "
        "c.academic_year_level_id) AS level_id, "
        "COALESCE(level_points.points, s.points, 0) AS points "
        "FROM behavior_attendance_statuses s "
        "JOIN behavior_configurations c ON c.id = s.behavior_configuration_id "
        "LEFT JOIN behavior_attendance_status_levels level_points "
        "ON level_points.behavior_attendance_status_id = s.id "
        "LEFT JOIN behavior_configuration_levels config_levels "
        "ON config_levels.behavior_configuration_id = s.behavior_configuration_id "
        "AND level_points.id IS NULL"
    )).mappings().all()
    for row in point_rows:
        if row["level_id"] is None:
            continue
        for shift in SHIFTS:
            connection.execute(text(
                "INSERT INTO behavior_attendance_status_shift_levels "
                "(behavior_attendance_status_id, behavior_configuration_id, academic_year_level_id, "
                "school_shift, points, created_at, updated_at) "
                "VALUES (:status_id, :configuration_id, :level_id, :shift, :points, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP) "
                "ON CONFLICT (behavior_configuration_id, academic_year_level_id, behavior_attendance_status_id, school_shift) DO NOTHING"
            ), {
                "status_id": row["status_id"],
                "configuration_id": row["configuration_id"],
                "level_id": row["level_id"],
                "shift": shift,
                "points": row["points"],
            })


def upgrade():
    app = create_app()
    with app.app_context():
        with db.engine.begin() as connection:
            AcademicYearLevelShiftAttendanceDay.__table__.create(bind=connection, checkfirst=True)
            BehaviorAttendanceStatusShiftLevel.__table__.create(bind=connection, checkfirst=True)
            _backfill(connection)
            _record_migration(connection)


def main():
    argparse.ArgumentParser(description="Add shift-specific Behavior Attendance settings").parse_args()
    upgrade()
    print(f"Applied {VERSION}")


if __name__ == "__main__":
    main()
