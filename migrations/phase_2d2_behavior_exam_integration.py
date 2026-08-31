"""Connect Behavior sessions to the canonical year-scoped Exam registry.

The migration is additive and preserves legacy ExamType-backed sessions. It
also makes old SQLite/PostgreSQL schemas able to store canonical exam links.
"""

import argparse
import os
import sys

from sqlalchemy import inspect, text

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import create_app, db
from app.models import BehaviorSession
from app.schema_compat import ensure_behavior_exam_scope


VERSION = "phase_2d2_behavior_exam_integration_v1"


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
        ensure_behavior_exam_scope()
        with db.engine.begin() as connection:
            if "behavior_sessions" not in inspect(connection).get_table_names():
                BehaviorSession.__table__.create(bind=connection, checkfirst=True)
            _record_migration(connection)


def main():
    argparse.ArgumentParser(
        description="Connect Behavior sessions to canonical year-scoped Exams"
    ).parse_args()
    upgrade()
    print(f"Applied {VERSION}; canonical Exam links are ready")


if __name__ == "__main__":
    main()
