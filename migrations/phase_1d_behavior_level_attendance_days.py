"""Add Academic Year + Academic Level scoped Attendance active days.

This migration is additive. Existing ``behavior_attendance_days`` rows are
used only to seed the new canonical schedule; no Attendance record or score is
rewritten.
"""

import argparse
import os
import sys

from sqlalchemy import text

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import create_app, db
from app.models import AcademicYearLevelAttendanceDay


VERSION = "phase_1d_behavior_level_attendance_days_v1"
WEEKDAY_LABELS = {
    0: "Monday",
    1: "Tuesday",
    2: "Wednesday",
    3: "Thursday",
    4: "Friday",
    5: "Saturday",
    6: "Sunday",
}
LEGACY_DEFAULT_ACTIVE_DAYS = {0, 1, 2, 3, 5, 6}


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


def _legacy_active_days(connection, level_id):
    config_id = connection.execute(text(
        "SELECT c.id FROM behavior_configurations c "
        "JOIN behavior_attendance_days d "
        "ON d.behavior_configuration_id = c.id "
        "WHERE c.academic_year_level_id = :level_id "
        "ORDER BY c.id LIMIT 1"
    ), {"level_id": level_id}).scalar()
    if config_id is None:
        return None
    rows = connection.execute(text(
        "SELECT weekday FROM behavior_attendance_days "
        "WHERE behavior_configuration_id = :config_id AND is_active = :active"
    ), {"config_id": config_id, "active": True}).scalars().all()
    return {int(value) for value in rows}


def _backfill(connection):
    level_ids = connection.execute(text(
        "SELECT id FROM academic_year_levels ORDER BY id"
    )).scalars().all()
    for level_id in level_ids:
        existing = {
            int(value) for value in connection.execute(text(
                "SELECT weekday FROM academic_year_level_attendance_days "
                "WHERE academic_year_level_id = :level_id"
            ), {"level_id": level_id}).scalars().all()
        }
        source_days = _legacy_active_days(connection, level_id)
        if source_days is None:
            has_configuration = connection.execute(text(
                "SELECT 1 FROM behavior_configurations "
                "WHERE academic_year_level_id = :level_id LIMIT 1"
            ), {"level_id": level_id}).scalar()
            if not has_configuration:
                continue
            # This preserves the established six-day default for existing
            # Behavior configurations, without becoming a runtime fallback.
            source_days = set(LEGACY_DEFAULT_ACTIVE_DAYS)

        for weekday, label in WEEKDAY_LABELS.items():
            if weekday in existing:
                continue
            connection.execute(text(
                "INSERT INTO academic_year_level_attendance_days "
                "(academic_year_level_id, weekday, label, is_active, created_at, updated_at) "
                "VALUES (:level_id, :weekday, :label, :is_active, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ), {
                "level_id": level_id,
                "weekday": weekday,
                "label": label,
                "is_active": weekday in source_days,
            })


def upgrade():
    app = create_app()
    with app.app_context():
        with db.engine.begin() as connection:
            AcademicYearLevelAttendanceDay.__table__.create(bind=connection, checkfirst=True)
            _backfill(connection)
            _record_migration(connection)


def main():
    argparse.ArgumentParser(
        description="Add level-scoped Behavior Attendance active days"
    ).parse_args()
    upgrade()
    print(f"Applied {VERSION}")


if __name__ == "__main__":
    main()
