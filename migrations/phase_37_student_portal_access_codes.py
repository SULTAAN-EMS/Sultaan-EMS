"""Add private student portal access codes and academic-scope policies."""

import argparse
import os
import sys

from sqlalchemy import text

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import create_app, db
from app.models import StudentPortalAccessCode, StudentPortalAccessPolicy


VERSION = "phase_37_student_portal_access_codes_v1"


def upgrade():
    app = create_app()
    with app.app_context():
        with db.engine.begin() as connection:
            if connection.dialect.name == "postgresql":
                connection.execute(text(
                    "SELECT pg_advisory_xact_lock(hashtext(:lock_name)::bigint)"
                ), {"lock_name": VERSION})
            connection.execute(text(
                "CREATE TABLE IF NOT EXISTS schema_migrations "
                "(version VARCHAR(120) PRIMARY KEY, applied_at TIMESTAMP NOT NULL)"
            ))
            StudentPortalAccessCode.__table__.create(bind=connection, checkfirst=True)
            StudentPortalAccessPolicy.__table__.create(bind=connection, checkfirst=True)
            if connection.dialect.name == "sqlite":
                statement = (
                    "INSERT OR IGNORE INTO schema_migrations (version, applied_at) "
                    "VALUES (:version, CURRENT_TIMESTAMP)"
                )
            else:
                statement = (
                    "INSERT INTO schema_migrations (version, applied_at) "
                    "VALUES (:version, CURRENT_TIMESTAMP) ON CONFLICT (version) DO NOTHING"
                )
            connection.execute(text(statement), {"version": VERSION})
    return {"status": "applied", "version": VERSION}


def main():
    argparse.ArgumentParser(description="Add student portal access-code tables").parse_args()
    result = upgrade()
    print(f"Applied {result['version']}")


if __name__ == "__main__":
    main()
