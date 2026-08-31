"""Add Phase 2C event-management integrity fields.

This migration is additive. It preserves existing Behavior events and adds
the immutable action-level snapshot plus an optional duplicate-submission key.
"""

import argparse
import os
import sys

from sqlalchemy import inspect, text

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import create_app, db
from app.models import BehaviorAction, BehaviorCategory, BehaviorConfiguration, BehaviorEvent, BehaviorSession


VERSION = "phase_2c_behavior_events_v1"
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
    if connection.dialect.name == "sqlite":
        connection.execute(
            text(
                "INSERT OR IGNORE INTO schema_migrations "
                "(version, applied_at) VALUES (:version, CURRENT_TIMESTAMP)"
            ),
            {"version": VERSION},
        )
    else:
        connection.execute(
            text(
                "INSERT INTO schema_migrations (version, applied_at) "
                "VALUES (:version, CURRENT_TIMESTAMP) ON CONFLICT (version) DO NOTHING"
            ),
            {"version": VERSION},
        )


def upgrade():
    app = create_app()
    with app.app_context():
        with db.engine.begin() as connection:
            existing = set(inspect(connection).get_table_names())
            missing = [table for table in BEHAVIOR_TABLES if table.name not in existing]
            if missing:
                db.metadata.create_all(bind=connection, tables=missing, checkfirst=True)

            columns = {item["name"] for item in inspect(connection).get_columns("behavior_events")}
            if "action_level_snapshot" not in columns:
                connection.execute(text(
                    "ALTER TABLE behavior_events "
                    "ADD COLUMN action_level_snapshot INTEGER NOT NULL DEFAULT 1"
                ))
            if "idempotency_key" not in columns:
                connection.execute(text(
                    "ALTER TABLE behavior_events ADD COLUMN idempotency_key VARCHAR(120)"
                ))
            connection.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_behavior_event_idempotency_key "
                "ON behavior_events (idempotency_key)"
            ))
            _record_migration(connection)


def main():
    parser = argparse.ArgumentParser(description="Add Phase 2C Behavior event integrity fields")
    parser.parse_args()
    upgrade()
    print(f"Applied {VERSION}; action snapshots and idempotency protection ready")


if __name__ == "__main__":
    main()
