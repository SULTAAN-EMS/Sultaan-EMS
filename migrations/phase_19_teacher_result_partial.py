"""Track draft, submitted, and approved status for each student mark."""

import argparse
import os
import sys

from sqlalchemy import inspect, text

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import create_app, db


VERSION = "phase_19_teacher_result_partial_v1"


def upgrade():
    app = create_app()
    with app.app_context():
        inspector = inspect(db.engine)
        required = {"teacher_result_submissions", "teacher_result_marks"}
        missing = required - set(inspector.get_table_names())
        if missing:
            raise RuntimeError(f"Required tables are missing before migration: {', '.join(sorted(missing))}")

        columns = {column["name"] for column in inspector.get_columns("teacher_result_marks")}
        added_status = "status" not in columns
        with db.engine.begin() as connection:
            if added_status:
                constraint = " CHECK (status IN ('draft', 'submitted', 'approved'))" if connection.dialect.name in {"sqlite", "postgresql"} else ""
                connection.execute(text(
                    "ALTER TABLE teacher_result_marks ADD COLUMN status VARCHAR(16) NOT NULL DEFAULT 'draft'" + constraint
                ))

                connection.execute(text("""
                    UPDATE teacher_result_marks
                    SET status = CASE
                        WHEN (SELECT status FROM teacher_result_submissions s
                              WHERE s.id = teacher_result_marks.submission_id) = 'submitted' THEN 'submitted'
                        WHEN (SELECT status FROM teacher_result_submissions s
                              WHERE s.id = teacher_result_marks.submission_id) = 'approved' THEN 'approved'
                        ELSE 'draft'
                    END
                """))
            connection.execute(text(
                "CREATE TABLE IF NOT EXISTS schema_migrations "
                "(version VARCHAR(120) PRIMARY KEY, applied_at TIMESTAMP NOT NULL)"
            ))
            dialect = connection.dialect.name
            if dialect == "postgresql":
                connection.execute(text(
                    "INSERT INTO schema_migrations (version, applied_at) "
                    "VALUES (:version, CURRENT_TIMESTAMP) ON CONFLICT (version) DO NOTHING"
                ), {"version": VERSION})
            elif dialect == "sqlite":
                connection.execute(text(
                    "INSERT OR IGNORE INTO schema_migrations (version, applied_at) "
                    "VALUES (:version, CURRENT_TIMESTAMP)"
                ), {"version": VERSION})
            else:
                connection.execute(text(
                    "INSERT IGNORE INTO schema_migrations (version, applied_at) "
                    "VALUES (:version, CURRENT_TIMESTAMP)"
                ), {"version": VERSION})
        return {"status": "applied", "columns": 1}


if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__).parse_args()
    print(upgrade())
