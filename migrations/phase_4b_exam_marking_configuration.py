"""Additive migration for exact Academic Year + Level + Exam mark defaults."""

import argparse
import os
import sys

from sqlalchemy import text

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import create_app, db
from app.models import ExamMarkingConfiguration


VERSION = "phase_4b_exam_marking_configuration_v1"


def upgrade():
    app = create_app()
    with app.app_context():
        ExamMarkingConfiguration.__table__.create(bind=db.engine, checkfirst=True)
        with db.engine.begin() as connection:
            connection.execute(text(
                "CREATE TABLE IF NOT EXISTS schema_migrations "
                "(version VARCHAR(120) PRIMARY KEY, applied_at TIMESTAMP NOT NULL)"
            ))
            if db.engine.dialect.name == "sqlite":
                connection.execute(
                    text(
                        "INSERT OR IGNORE INTO schema_migrations "
                        "(version, applied_at) VALUES (:version, CURRENT_TIMESTAMP)"
                    ),
                    {"version": VERSION},
                )
            else:
                connection.execute(
                    text(
                        "INSERT INTO schema_migrations (version, applied_at) "
                        "VALUES (:version, CURRENT_TIMESTAMP) "
                        "ON CONFLICT (version) DO NOTHING"
                    ),
                    {"version": VERSION},
                )


def main():
    parser = argparse.ArgumentParser(description="Apply the exam marking configuration migration")
    parser.parse_args()
    upgrade()
    print(f"Applied {VERSION}; table: {ExamMarkingConfiguration.__tablename__}")


if __name__ == "__main__":
    main()
