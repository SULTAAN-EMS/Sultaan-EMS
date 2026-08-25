"""Additive Phase 4A exam-aware Promotion Rules migration.

The migration keeps existing Phase 3B rules as legacy rows (``exam_id`` is
NULL), adds the explicit final-evaluation flag to Results ``exams``, and
allows one rule per Academic Year + Academic Year Level + Exam.

No results, enrollments, evaluations, or movement history are rewritten.
"""

import argparse
import os
import sys

from sqlalchemy import inspect, text

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import create_app, db


VERSION = "phase_4a_exam_aware_promotion_rules_v1"


def _postgres_constraints(connection, table_name):
    rows = connection.execute(
        text(
            "SELECT conname FROM pg_constraint "
            "WHERE conrelid = CAST(:table_name AS regclass)"
        ),
        {"table_name": table_name},
    )
    return {row[0] for row in rows}


def _add_column(connection, table_name, column_name, definition):
    columns = {row[1] for row in connection.execute(text(f"PRAGMA table_info({table_name})"))}
    if column_name not in columns:
        connection.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}"))


def _ensure_sqlite_columns(connection):
    # SQLite is used for local development. Fresh databases get the current
    # model shape from db.create_all; existing local databases still receive
    # the new columns without touching any application data.
    _add_column(connection, "exams", "is_final_evaluation", "BOOLEAN NOT NULL DEFAULT 0")
    _add_column(connection, "promotion_rules", "exam_id", "INTEGER")


def upgrade():
    app = create_app()
    with app.app_context():
        db.create_all()
        with db.engine.begin() as connection:
            dialect = db.engine.dialect.name
            if dialect == "sqlite":
                _ensure_sqlite_columns(connection)
            elif dialect == "postgresql":
                inspector = inspect(db.engine)
                exam_columns = {item["name"] for item in inspector.get_columns("exams")}
                if "is_final_evaluation" not in exam_columns:
                    connection.execute(
                        text(
                            "ALTER TABLE exams ADD COLUMN is_final_evaluation "
                            "BOOLEAN NOT NULL DEFAULT FALSE"
                        )
                    )
                rule_columns = {item["name"] for item in inspector.get_columns("promotion_rules")}
                if "exam_id" not in rule_columns:
                    connection.execute(
                        text("ALTER TABLE promotion_rules ADD COLUMN exam_id INTEGER")
                    )
                constraints = _postgres_constraints(connection, "promotion_rules")
                if "uq_promotion_rule_year_level" in constraints:
                    connection.execute(
                        text(
                            "ALTER TABLE promotion_rules DROP CONSTRAINT "
                            "uq_promotion_rule_year_level"
                        )
                    )
                constraints = _postgres_constraints(connection, "promotion_rules")
                if "uq_promotion_rule_year_level_exam" not in constraints:
                    connection.execute(
                        text(
                            "ALTER TABLE promotion_rules ADD CONSTRAINT "
                            "uq_promotion_rule_year_level_exam UNIQUE "
                            "(academic_year_id, academic_year_level_id, exam_id)"
                        )
                    )
                if "fk_promotion_rules_exam_id" not in constraints:
                    connection.execute(
                        text(
                            "ALTER TABLE promotion_rules ADD CONSTRAINT "
                            "fk_promotion_rules_exam_id FOREIGN KEY (exam_id) "
                            "REFERENCES exams(id) ON DELETE SET NULL"
                        )
                    )
                connection.execute(
                    text(
                        "CREATE INDEX IF NOT EXISTS idx_promotion_rules_exam_id "
                        "ON promotion_rules (exam_id)"
                    )
                )
                connection.execute(
                    text(
                        "CREATE INDEX IF NOT EXISTS idx_exams_final_evaluation "
                        "ON exams (is_final_evaluation)"
                    )
                )
            else:
                raise RuntimeError(f"Unsupported Phase 4A database dialect: {dialect}")

            connection.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS schema_migrations "
                    "(version VARCHAR(120) PRIMARY KEY, applied_at TIMESTAMP NOT NULL)"
                )
            )
            if dialect == "sqlite":
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
        return VERSION


def main():
    argparse.ArgumentParser(
        description="Apply the Phase 4A exam-aware Promotion Rules migration"
    ).parse_args()
    print(f"Applied {upgrade()}")


if __name__ == "__main__":
    main()
