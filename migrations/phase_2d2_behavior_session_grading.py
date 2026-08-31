"""Upgrade Behavior grading to session-owned raw score bands.

The normal application startup runs the same idempotent compatibility helper,
which is useful for Render/SQLite deployments that do not run a pre-deploy
command. This explicit migration is provided for operators who use a migration
runner and records a separate schema version.
"""

import argparse
import os
import sys

from sqlalchemy import text

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import create_app, db
from app.schema_compat import ensure_behavior_session_grading


VERSION = "phase_2d2_behavior_session_grading_v1"


def upgrade():
    app = create_app()
    with app.app_context():
        ensure_behavior_session_grading()
        with db.engine.begin() as connection:
            connection.execute(text(
                "CREATE TABLE IF NOT EXISTS schema_migrations "
                "(version VARCHAR(120) PRIMARY KEY, applied_at TIMESTAMP NOT NULL)"
            ))
            if connection.dialect.name == "sqlite":
                connection.execute(text(
                    "INSERT OR IGNORE INTO schema_migrations "
                    "(version, applied_at) VALUES (:version, CURRENT_TIMESTAMP)"
                ), {"version": VERSION})
            else:
                connection.execute(text(
                    "INSERT INTO schema_migrations (version, applied_at) "
                    "VALUES (:version, CURRENT_TIMESTAMP) ON CONFLICT (version) DO NOTHING"
                ), {"version": VERSION})


def main():
    argparse.ArgumentParser(
        description="Make Behavior grade ranges session-owned raw-score bands"
    ).parse_args()
    upgrade()
    print(f"Applied {VERSION}; ordinary GradeScale data was not changed")


if __name__ == "__main__":
    main()
