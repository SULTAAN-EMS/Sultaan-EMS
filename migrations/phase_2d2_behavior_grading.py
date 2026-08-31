"""Create the isolated Behavior-owned grade scale registry.

This migration is additive. It does not alter ordinary GradeScale rows or
change any existing Result/subject data.
"""

import argparse
import os
import sys

from sqlalchemy import inspect, text

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import create_app, db
from app.models import BehaviorGradeScale


VERSION = "phase_2d2_behavior_grading_v1"


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
            if "behavior_grade_scales" not in inspect(connection).get_table_names():
                BehaviorGradeScale.__table__.create(bind=connection, checkfirst=True)
            _record_migration(connection)


def main():
    argparse.ArgumentParser(
        description="Create the isolated Behavior-owned grade scale registry"
    ).parse_args()
    upgrade()
    print(f"Applied {VERSION}; ordinary GradeScale data was not changed")


if __name__ == "__main__":
    main()
