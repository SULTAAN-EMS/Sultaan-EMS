"""Add normalized Academic-Year-Level membership to Behavior configurations."""

import argparse
import os
import sys

from sqlalchemy import text

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import create_app, db
from app.schema_compat import ensure_behavior_configuration_levels


VERSION = "phase_3_behavior_configuration_levels_v1"


def upgrade():
    app = create_app()
    with app.app_context():
        ensure_behavior_configuration_levels()
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
        description="Add Academic Level membership to Behavior configurations"
    ).parse_args()
    upgrade()
    print(f"Applied {VERSION}; existing Behavior configurations were preserved")


if __name__ == "__main__":
    main()
