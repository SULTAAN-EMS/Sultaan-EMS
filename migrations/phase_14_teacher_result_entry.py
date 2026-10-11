"""Create assignment-scoped, reviewable teacher result-entry tables."""

import argparse
import os
import sys

from sqlalchemy import inspect, text

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import create_app, db
from app.models import TeacherResultAssignment, TeacherResultMark, TeacherResultSubmission


VERSION = "phase_14_teacher_result_entry_v1"


def upgrade():
    app = create_app()
    with app.app_context():
        required = {"teachers", "academic_years", "exams", "academic_classes", "subjects", "students", "users"}
        missing = required - set(inspect(db.engine).get_table_names())
        if missing:
            raise RuntimeError(f"Required tables are missing before migration: {', '.join(sorted(missing))}")

        for model in (TeacherResultAssignment, TeacherResultSubmission, TeacherResultMark):
            model.__table__.create(bind=db.engine, checkfirst=True)

        with db.engine.begin() as connection:
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

        return {"status": "applied", "tables": 3}


if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__).parse_args()
    print(upgrade())
