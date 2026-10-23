"""Repair unambiguous teacher-scope class and subject bridge mismatches."""

import argparse
import os
import sys

from sqlalchemy import inspect, text

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import create_app, db
from app.academic_hierarchy import repair_wrong_level_year_scope_bridges


VERSION = "phase_26_repair_wrong_level_bridges_v1"


def _record_migration(connection):
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


def upgrade():
    app = create_app()
    with app.app_context():
        required = {
            "academic_year_levels",
            "academic_year_classes",
            "academic_year_subjects",
            "academic_levels",
            "academic_classes",
            "subjects",
        }
        missing = required - set(inspect(db.engine).get_table_names())
        if missing:
            raise RuntimeError(
                "Required tables are missing before migration: "
                + ", ".join(sorted(missing))
            )

        with db.engine.begin() as connection:
            connection.execute(text(
                "CREATE TABLE IF NOT EXISTS schema_migrations "
                "(version VARCHAR(120) PRIMARY KEY, applied_at TIMESTAMP NOT NULL)"
            ))
            already_applied = connection.execute(
                text("SELECT 1 FROM schema_migrations WHERE version = :version"),
                {"version": VERSION},
            ).first()
        if already_applied:
            return {"status": "already_applied"}

        report = repair_wrong_level_year_scope_bridges()
        with db.engine.begin() as connection:
            _record_migration(connection)
        return {"status": "applied", **{key: len(value) for key, value in report.items()}}


if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__).parse_args()
    print(upgrade())
