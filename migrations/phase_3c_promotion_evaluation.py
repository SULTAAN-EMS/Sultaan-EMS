"""Additive Phase 3C columns for explicit promotion evaluation history.

Existing Phase 3B snapshots are retained.  New evaluations record the exact
exam context and an explicit status so incomplete or invalid data cannot be
mistaken for a PASS/FAIL outcome.
"""

import argparse
import os
import sys

from sqlalchemy import inspect, text

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import create_app, db


VERSION = "phase_3c_promotion_evaluation_v1"


def _ensure_ledger(connection, dialect_name):
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


def _add_column(connection, inspector, dialect_name, column, definition):
    if column in {item["name"] for item in inspector.get_columns("promotion_evaluations")}:
        return
    # Existing rows need a concrete status.  The selected exam is nullable
    # because Phase 3B snapshots predate the explicit context requirement.
    connection.execute(text(f"ALTER TABLE promotion_evaluations ADD COLUMN {column} {definition}"))


def _postgresql_constraints(connection):
    rows = connection.execute(text(
        "SELECT conname FROM pg_constraint "
        "WHERE conrelid = 'promotion_evaluations'::regclass"
    ))
    return {row[0] for row in rows}


def upgrade():
    app = create_app()
    with app.app_context():
        inspector = inspect(db.engine)
        if not inspector.has_table("promotion_evaluations"):
            db.create_all()
        with db.engine.begin() as connection:
            inspector = inspect(db.engine)
            dialect_name = db.engine.dialect.name
            _add_column(connection, inspector, dialect_name, "exam_id", "INTEGER")
            inspector = inspect(db.engine)
            _add_column(
                connection,
                inspector,
                dialect_name,
                "evaluation_status",
                "VARCHAR(20) NOT NULL DEFAULT 'EVALUATED'",
            )
            if dialect_name == "postgresql":
                connection.execute(text(
                    "ALTER TABLE promotion_evaluations "
                    "ALTER COLUMN overall_percentage DROP NOT NULL, "
                    "ALTER COLUMN base_outcome DROP NOT NULL, "
                    "ALTER COLUMN final_outcome DROP NOT NULL"
                ))
                constraints = _postgresql_constraints(connection)
                for name in (
                    "ck_promotion_evaluation_base_outcome",
                    "ck_promotion_evaluation_final_outcome",
                ):
                    if name in constraints:
                        connection.execute(text(f"ALTER TABLE promotion_evaluations DROP CONSTRAINT {name}"))
                connection.execute(text(
                    "ALTER TABLE promotion_evaluations ADD CONSTRAINT "
                    "ck_promotion_evaluation_base_outcome "
                    "CHECK (base_outcome IS NULL OR base_outcome IN ('PASS', 'FAIL'))"
                ))
                connection.execute(text(
                    "ALTER TABLE promotion_evaluations ADD CONSTRAINT "
                    "ck_promotion_evaluation_final_outcome "
                    "CHECK (final_outcome IS NULL OR final_outcome IN ('PASS', 'FAIL'))"
                ))
                constraints = _postgresql_constraints(connection)
                if "ck_promotion_evaluation_status" not in constraints:
                    connection.execute(text(
                        "ALTER TABLE promotion_evaluations ADD CONSTRAINT "
                        "ck_promotion_evaluation_status CHECK "
                        "(evaluation_status IN ('EVALUATED', 'INCOMPLETE', 'INVALID', 'NOT_EVALUATED'))"
                    ))
                if "fk_promotion_evaluations_exam_id" not in constraints:
                    connection.execute(text(
                        "ALTER TABLE promotion_evaluations ADD CONSTRAINT "
                        "fk_promotion_evaluations_exam_id FOREIGN KEY (exam_id) "
                        "REFERENCES exams(id) ON DELETE SET NULL"
                    ))
                connection.execute(text(
                    "CREATE INDEX IF NOT EXISTS idx_promotion_evaluations_exam_id "
                    "ON promotion_evaluations (exam_id)"
                ))
                connection.execute(text(
                    "CREATE INDEX IF NOT EXISTS idx_promotion_evaluations_status "
                    "ON promotion_evaluations (evaluation_status)"
                ))
            _ensure_ledger(connection, dialect_name)
        return VERSION


def main():
    argparse.ArgumentParser(description="Apply the Phase 3C promotion evaluation migration").parse_args()
    print(f"Applied {upgrade()}")


if __name__ == "__main__":
    main()
