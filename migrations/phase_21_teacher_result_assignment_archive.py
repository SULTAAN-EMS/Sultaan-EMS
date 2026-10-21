"""Add reversible archive state to teacher result assignments."""

import argparse
import os
import sys

from sqlalchemy import inspect, text

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import create_app, db


VERSION = "phase_21_teacher_result_assignment_archive_v1"


def upgrade():
    app = create_app()
    with app.app_context():
        inspector = inspect(db.engine)
        if "teacher_result_assignments" not in inspector.get_table_names():
            raise RuntimeError("Required table teacher_result_assignments is missing before migration.")

        columns = {column["name"] for column in inspector.get_columns("teacher_result_assignments")}
        with db.engine.begin() as connection:
            if "deleted_at" not in columns:
                connection.execute(text(
                    "ALTER TABLE teacher_result_assignments ADD COLUMN deleted_at TIMESTAMP NULL"
                ))
            if "deleted_previous_is_active" not in columns:
                connection.execute(text(
                    "ALTER TABLE teacher_result_assignments ADD COLUMN deleted_previous_is_active BOOLEAN NULL"
                ))
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

        return {"status": "applied", "columns": 2}


if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__).parse_args()
    print(upgrade())
