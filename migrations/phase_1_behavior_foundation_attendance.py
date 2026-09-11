"""Install the additive Phase 1 Behavior taxonomy and daily attendance schema."""

import argparse
import os
import sys

from sqlalchemy import inspect, text

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import create_app, db
from app.models import (
    BehaviorActionChoice,
    BehaviorAttendanceDay,
    BehaviorAttendanceRecord,
    BehaviorAttendanceStatus,
    BehaviorSubCategory,
)


VERSION = "phase_1_behavior_foundation_attendance_v1"


def _add_column(connection, table, name, definition):
    if table not in inspect(connection).get_table_names():
        return
    columns = {item["name"] for item in inspect(connection).get_columns(table)}
    if name not in columns:
        connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {definition}"))


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


def upgrade():
    app = create_app()
    with app.app_context():
        with db.engine.begin() as connection:
            _add_column(connection, "behavior_actions", "behavior_subcategory_id", "INTEGER")
            _add_column(connection, "behavior_actions", "behavior_type", "VARCHAR(30) NOT NULL DEFAULT 'direct_action'")
            _add_column(connection, "behavior_events", "behavior_action_choice_id", "INTEGER")
            _add_column(connection, "behavior_configurations", "behavior_attendance_scoring_enabled", "BOOLEAN NOT NULL DEFAULT TRUE")
            for model in (
                BehaviorSubCategory,
                BehaviorActionChoice,
                BehaviorAttendanceStatus,
                BehaviorAttendanceDay,
                BehaviorAttendanceRecord,
            ):
                model.__table__.create(bind=connection, checkfirst=True)
            _record_migration(connection)


def main():
    argparse.ArgumentParser(description="Install Phase 1 Behavior foundation and attendance").parse_args()
    upgrade()
    print(f"Applied {VERSION}")


if __name__ == "__main__":
    main()
