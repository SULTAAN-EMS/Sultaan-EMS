"""Add optional Whole Class Results PDF labels to year-scoped subjects."""

import argparse
import os
import sys

from sqlalchemy import inspect, text

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import create_app, db


VERSION = "phase_44_subject_pdf_short_names_v1"


def upgrade():
    app = create_app()
    with app.app_context():
        with db.engine.begin() as connection:
            dialect = connection.dialect.name
            inspector = inspect(connection)
            if inspector.has_table("academic_year_subjects"):
                columns = {row["name"] for row in inspector.get_columns("academic_year_subjects")}
                if "pdf_short_name" not in columns:
                    if dialect == "postgresql":
                        connection.execute(text(
                            "ALTER TABLE academic_year_subjects "
                            "ADD COLUMN IF NOT EXISTS pdf_short_name VARCHAR(40)"
                        ))
                    else:
                        connection.execute(text(
                            "ALTER TABLE academic_year_subjects "
                            "ADD COLUMN pdf_short_name VARCHAR(40)"
                        ))

            connection.execute(text(
                "CREATE TABLE IF NOT EXISTS schema_migrations "
                "(version VARCHAR(120) PRIMARY KEY, applied_at TIMESTAMP NOT NULL)"
            ))
            if dialect == "sqlite":
                connection.execute(text(
                    "INSERT OR IGNORE INTO schema_migrations (version, applied_at) "
                    "VALUES (:version, CURRENT_TIMESTAMP)"
                ), {"version": VERSION})
            elif dialect in {"mysql", "mariadb"}:
                connection.execute(text(
                    "INSERT IGNORE INTO schema_migrations (version, applied_at) "
                    "VALUES (:version, CURRENT_TIMESTAMP)"
                ), {"version": VERSION})
            else:
                connection.execute(text(
                    "INSERT INTO schema_migrations (version, applied_at) "
                    "VALUES (:version, CURRENT_TIMESTAMP) "
                    "ON CONFLICT (version) DO NOTHING"
                ), {"version": VERSION})

        return {"status": "applied", "version": VERSION}


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    result = upgrade()
    print(f"Applied {result['version']} ({result['status']})")


if __name__ == "__main__":
    main()
