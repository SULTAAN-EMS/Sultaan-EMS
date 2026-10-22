"""Create the immutable per-student teacher score revision journal."""

import argparse
import os
import sys

from sqlalchemy import inspect, text

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import create_app, db
from app.models import TeacherResultMarkRevision


VERSION = "phase_24_teacher_result_mark_revisions_v1"


def upgrade():
    app = create_app()
    with app.app_context():
        tables = set(inspect(db.engine).get_table_names())
        required = {"teacher_result_marks", "teacher_result_submissions", "teacher_result_assignments", "students", "users"}
        missing = required - tables
        if missing:
            raise RuntimeError(
                "Required tables are missing before migration: " + ", ".join(sorted(missing))
            )

        with db.engine.begin() as connection:
            TeacherResultMarkRevision.__table__.create(bind=connection, checkfirst=True)
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

        return {"status": "applied", "table": "teacher_result_mark_revisions"}


if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__).parse_args()
    print(upgrade())
