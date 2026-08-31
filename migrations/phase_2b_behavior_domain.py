"""Create the dedicated, year-aware Behavior administration domain.

The migration is additive. It reuses the existing academic hierarchy and
creates no ordinary Result, Attendance, Promotion, or Incident records.
"""

import argparse
import os
import sys

from sqlalchemy import inspect, text

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import create_app, db
from app.models import (
    BehaviorAction,
    BehaviorCategory,
    BehaviorConfiguration,
    BehaviorEvent,
    BehaviorSession,
)


VERSION = "phase_2b_behavior_domain_v1"
BEHAVIOR_TABLES = (
    BehaviorConfiguration.__table__,
    BehaviorSession.__table__,
    BehaviorCategory.__table__,
    BehaviorAction.__table__,
    BehaviorEvent.__table__,
)


def _record_migration(connection):
    connection.execute(text(
        "CREATE TABLE IF NOT EXISTS schema_migrations "
        "(version VARCHAR(120) PRIMARY KEY, applied_at TIMESTAMP NOT NULL)"
    ))
    dialect = connection.dialect.name
    if dialect == "sqlite":
        connection.execute(text(
            "INSERT OR IGNORE INTO schema_migrations "
            "(version, applied_at) VALUES (:version, CURRENT_TIMESTAMP)"
        ), {"version": VERSION})
    elif dialect == "postgresql":
        connection.execute(text(
            "INSERT INTO schema_migrations (version, applied_at) "
            "VALUES (:version, CURRENT_TIMESTAMP) ON CONFLICT (version) DO NOTHING"
        ), {"version": VERSION})
    else:
        connection.execute(text(
            "INSERT IGNORE INTO schema_migrations "
            "(version, applied_at) VALUES (:version, CURRENT_TIMESTAMP)"
        ), {"version": VERSION})


def upgrade():
    app = create_app()
    with app.app_context():
        with db.engine.begin() as connection:
            existing = set(inspect(connection).get_table_names())
            missing = [table for table in BEHAVIOR_TABLES if table.name not in existing]
            if missing:
                db.metadata.create_all(bind=connection, tables=missing, checkfirst=True)
            _record_migration(connection)


def main():
    parser = argparse.ArgumentParser(description="Create the Phase 2B Behavior domain")
    parser.parse_args()
    upgrade()
    print(f"Applied {VERSION}; tables: {', '.join(table.name for table in BEHAVIOR_TABLES)}")


if __name__ == "__main__":
    main()
