"""Controlled Phase 2D migration for enrollment movement history.

This migration is additive and must be run explicitly in staging after the
Phase 2B enrollment table exists. It creates no replacement for legacy
placement fields and does not rewrite students, results, or attendance.
"""

import argparse
import os
import sys

from sqlalchemy import text

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import create_app, db
from app.models import StudentEnrollmentMovement


VERSION = "phase_2d_enrollment_movements_v1"


def upgrade():
    """Create the movement ledger and record the idempotent migration version."""
    app = create_app()
    with app.app_context():
        engine = db.engine
        StudentEnrollmentMovement.__table__.create(bind=engine, checkfirst=True)
        with engine.begin() as connection:
            connection.execute(text(
                "CREATE TABLE IF NOT EXISTS schema_migrations "
                "(version VARCHAR(120) PRIMARY KEY, applied_at TIMESTAMP NOT NULL)"
            ))
            if engine.dialect.name == "sqlite":
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
                        "VALUES (:version, CURRENT_TIMESTAMP) "
                        "ON CONFLICT (version) DO NOTHING"
                    ),
                    {"version": VERSION},
                )


def main():
    parser = argparse.ArgumentParser(description="Apply the Phase 2D movement-history migration")
    parser.parse_args()
    upgrade()
    print(f"Applied {VERSION}; table: {StudentEnrollmentMovement.__tablename__}")


if __name__ == "__main__":
    main()
