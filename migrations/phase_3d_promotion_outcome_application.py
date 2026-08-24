"""Additive Phase 3D ledger for applying immutable promotion evaluations.

The migration creates only the new application/audit table. Existing
PromotionEvaluation snapshots, StudentEnrollment rows, results, and
attendance records are not rewritten.
"""

import argparse
import os
import sys

from sqlalchemy import inspect, text

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import create_app, db


VERSION = "phase_3d_promotion_outcome_application_v1"


def upgrade():
    app = create_app()
    with app.app_context():
        # db.create_all is additive and uses the mapped constraints/FKs for
        # both SQLite rehearsal databases and PostgreSQL staging.
        db.create_all()
        inspector = inspect(db.engine)
        if not inspector.has_table("promotion_outcome_applications"):
            raise RuntimeError("Phase 3D application table was not created")
        with db.engine.begin() as connection:
            dialect_name = db.engine.dialect.name
            connection.execute(text(
                "CREATE TABLE IF NOT EXISTS schema_migrations "
                "(version VARCHAR(120) PRIMARY KEY, applied_at TIMESTAMP NOT NULL)"
            ))
            if dialect_name == "sqlite":
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
        return VERSION


def main():
    argparse.ArgumentParser(description="Apply Phase 3D promotion outcome application migration").parse_args()
    print(f"Applied {upgrade()}")


if __name__ == "__main__":
    main()
