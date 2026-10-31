"""Add date windows and an audit log for Behavior session revisions."""

import argparse
import os
import sys

from sqlalchemy import inspect, text

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import create_app, db
from app.models import BehaviorSessionRevision


VERSION = "phase_29_behavior_session_revisions_v1"
REQUIRED_TABLES = {"behavior_sessions", "users"}


def upgrade():
    app = create_app()
    with app.app_context():
        with db.engine.begin() as connection:
            inspector = inspect(connection)
            missing = REQUIRED_TABLES - set(inspector.get_table_names())
            if missing:
                raise RuntimeError(
                    "Required tables are missing before migration: "
                    + ", ".join(sorted(missing))
                )

            if connection.dialect.name == "postgresql":
                connection.execute(text(
                    "SELECT pg_advisory_xact_lock(hashtext(:lock_name)::bigint)"
                ), {"lock_name": VERSION})

            connection.execute(text(
                "CREATE TABLE IF NOT EXISTS schema_migrations "
                "(version VARCHAR(120) PRIMARY KEY, applied_at TIMESTAMP NOT NULL)"
            ))
            if connection.execute(
                text("SELECT 1 FROM schema_migrations WHERE version = :version"),
                {"version": VERSION},
            ).first():
                return {"status": "already_applied"}

            existing_columns = {
                column["name"]
                for column in inspect(connection).get_columns("behavior_sessions")
            }
            for name in ("period_start_date", "period_end_date"):
                if name not in existing_columns:
                    connection.execute(text(
                        f"ALTER TABLE behavior_sessions ADD COLUMN {name} DATE"
                    ))

            BehaviorSessionRevision.__table__.create(bind=connection, checkfirst=True)
            dialect = connection.dialect.name
            if dialect == "postgresql":
                statement = (
                    "INSERT INTO schema_migrations (version, applied_at) "
                    "VALUES (:version, CURRENT_TIMESTAMP) ON CONFLICT (version) DO NOTHING"
                )
            elif dialect == "sqlite":
                statement = (
                    "INSERT OR IGNORE INTO schema_migrations (version, applied_at) "
                    "VALUES (:version, CURRENT_TIMESTAMP)"
                )
            else:
                statement = (
                    "INSERT IGNORE INTO schema_migrations (version, applied_at) "
                    "VALUES (:version, CURRENT_TIMESTAMP)"
                )
            connection.execute(text(statement), {"version": VERSION})

        return {
            "status": "applied",
            "table": "behavior_session_revisions",
            "columns": ["period_start_date", "period_end_date"],
        }


if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__).parse_args()
    print(upgrade())
