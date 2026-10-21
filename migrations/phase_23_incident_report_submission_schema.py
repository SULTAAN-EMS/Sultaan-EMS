"""Upgrade incident-report storage for multi-category submissions."""

import argparse
import os
import sys

from sqlalchemy import inspect, text

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import create_app, db
from app.models import IncidentReportCategory


VERSION = "phase_23_incident_report_submission_schema_v1"

REPORT_COLUMNS = {
    "signature_data": "TEXT NULL",
    "other_description": "VARCHAR(500) NULL",
    "category_description": "VARCHAR(500) NULL",
    "action_description": "VARCHAR(500) NULL",
}


def upgrade():
    app = create_app()
    with app.app_context():
        with db.engine.begin() as connection:
            inspector = inspect(connection)
            tables = set(inspector.get_table_names())
            required = {"incident_reports", "incident_categories", "severity_levels"}
            missing = required - tables
            if missing:
                raise RuntimeError(
                    "Required incident-report tables are missing before migration: "
                    + ", ".join(sorted(missing))
                )

            columns = {column["name"] for column in inspector.get_columns("incident_reports")}
            added_columns = []
            for name, definition in REPORT_COLUMNS.items():
                if name not in columns:
                    connection.execute(text(
                        f"ALTER TABLE incident_reports ADD COLUMN {name} {definition}"
                    ))
                    added_columns.append(name)

            relation_created = "incident_report_categories" not in tables
            IncidentReportCategory.__table__.create(bind=connection, checkfirst=True)

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

        return {
            "status": "applied",
            "added_columns": added_columns,
            "created_category_table": relation_created,
        }


if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__).parse_args()
    print(upgrade())
