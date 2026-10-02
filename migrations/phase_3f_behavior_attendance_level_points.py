"""Add Academic-Year-Level scoped Attendance status points.

The old ``behavior_attendance_statuses.points`` column remains as a legacy
fallback. New Attendance marks use the override for the enrolled level, so
levels with six and five active school days can use different daily values.
"""

import argparse
import os
import sys

from sqlalchemy import text

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import create_app, db
from app.models import BehaviorAttendanceStatusLevel


VERSION = "phase_3f_behavior_attendance_level_points_v1"


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
    statuses = connection.execute(text(
        "SELECT id, behavior_configuration_id, points "
        "FROM behavior_attendance_statuses ORDER BY id"
    )).mappings().all()
    for status in statuses:
        level_ids = connection.execute(text(
            "SELECT academic_year_level_id FROM behavior_configuration_levels "
            "WHERE behavior_configuration_id = :configuration_id"
        ), {"configuration_id": status["behavior_configuration_id"]}).scalars().all()
        if not level_ids:
            level_id = connection.execute(text(
                "SELECT academic_year_level_id FROM behavior_configurations "
                "WHERE id = :configuration_id"
            ), {"configuration_id": status["behavior_configuration_id"]}).scalar()
            level_ids = [level_id] if level_id is not None else []
        for level_id in level_ids:
            exists = connection.execute(text(
                "SELECT 1 FROM behavior_attendance_status_levels "
                "WHERE behavior_attendance_status_id = :status_id "
                "AND academic_year_level_id = :level_id LIMIT 1"
            ), {"status_id": status["id"], "level_id": level_id}).scalar()
            if exists:
                continue
            connection.execute(text(
                "INSERT INTO behavior_attendance_status_levels "
                "(behavior_attendance_status_id, behavior_configuration_id, "
                "academic_year_level_id, points, created_at, updated_at) "
                "VALUES (:status_id, :configuration_id, :level_id, :points, "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ), {
                "status_id": status["id"],
                "configuration_id": status["behavior_configuration_id"],
                "level_id": level_id,
                "points": status["points"] or 0,
            })


def upgrade():
    app = create_app()
    with app.app_context():
        with db.engine.begin() as connection:
            BehaviorAttendanceStatusLevel.__table__.create(bind=connection, checkfirst=True)
            _backfill(connection)
            _record_migration(connection)


def main():
    argparse.ArgumentParser(
        description="Add level-scoped Behavior Attendance status points"
    ).parse_args()
    upgrade()
    print(f"Applied {VERSION}")


if __name__ == "__main__":
    main()
