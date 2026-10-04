"""Add class and Behavior-session shift scopes for morning/afternoon plans."""

import argparse
import os
import sys

from sqlalchemy import inspect, text

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import create_app, db


VERSION = "phase_11_behavior_class_shifts_v1"


def _record_migration(connection):
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
            "INSERT OR IGNORE INTO schema_migrations "
            "(version, applied_at) VALUES (:version, CURRENT_TIMESTAMP)"
        ), {"version": VERSION})
    else:
        connection.execute(text(
            "INSERT IGNORE INTO schema_migrations "
            "(version, applied_at) VALUES (:version, CURRENT_TIMESTAMP)"
        ), {"version": VERSION})


def upgrade():
    app = create_app()
    with app.app_context():
        inspector = inspect(db.engine)
        required_tables = {"academic_year_classes", "behavior_sessions"}
        if not required_tables.issubset(set(inspector.get_table_names())):
            raise RuntimeError("Academic class and Behavior session tables must exist before migration")

        added_columns = []
        with db.engine.begin() as connection:
            for table, column, definition in (
                ("academic_year_classes", "school_shift", "VARCHAR(20) NULL"),
                ("behavior_sessions", "applicable_shift", "VARCHAR(20) NOT NULL DEFAULT 'all'"),
            ):
                existing = {item["name"] for item in inspect(db.engine).get_columns(table)}
                if column not in existing:
                    connection.execute(text(
                        f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
                    ))
                    added_columns.append(f"{table}.{column}")

            _record_migration(connection)

        return {"status": "applied", "added_columns": added_columns}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    print(upgrade())
