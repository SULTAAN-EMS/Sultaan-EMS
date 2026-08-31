"""Add year-aware examination/behavior subject classification.

This migration is intentionally additive. Existing academic-year subjects are
classified as examination subjects so the legacy results bridge remains
unchanged. Behavior subjects can then be created without a legacy Subject row.
"""

import argparse
import os
import sys

from sqlalchemy import inspect, text

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import create_app, db


VERSION = "phase_2a_behavior_subject_classification_v1"
TABLE = "academic_year_subjects"
INDEX = "ix_academic_year_subject_scope_kind"


def _ensure_subject_kind_column(connection):
    inspector = inspect(connection)
    if TABLE not in inspector.get_table_names():
        return False
    columns = {column["name"] for column in inspector.get_columns(TABLE)}
    if "subject_kind" not in columns:
        connection.execute(text(
            "ALTER TABLE academic_year_subjects "
            "ADD COLUMN subject_kind VARCHAR(20) NOT NULL DEFAULT 'exam'"
        ))
    # The column is new to the Phase 1D schema. Any legacy/blank value is
    # deliberately preserved as the compatible examination classification.
    connection.execute(text(
        "UPDATE academic_year_subjects "
        "SET subject_kind = 'exam' "
        "WHERE subject_kind IS NULL OR TRIM(subject_kind) = '' "
        "OR subject_kind NOT IN ('exam', 'behavior')"
    ))
    return True


def _ensure_index(connection):
    inspector = inspect(connection)
    existing = {item.get("name") for item in inspector.get_indexes(TABLE)}
    if INDEX not in existing:
        connection.execute(text(
            "CREATE INDEX ix_academic_year_subject_scope_kind "
            "ON academic_year_subjects (academic_year_id, academic_year_level_id, subject_kind)"
        ))


def _ensure_kind_guard(connection):
    dialect = connection.dialect.name
    if dialect == "postgresql":
        exists = connection.execute(text(
            "SELECT 1 FROM pg_constraint "
            "WHERE conname = 'ck_academic_year_subject_kind'"
        )).first()
        if not exists:
            connection.execute(text(
                "ALTER TABLE academic_year_subjects ADD CONSTRAINT "
                "ck_academic_year_subject_kind CHECK "
                "(subject_kind IS NOT NULL AND subject_kind IN ('exam', 'behavior'))"
            ))
    elif dialect == "sqlite":
        # SQLite cannot add a CHECK constraint to an existing table without a
        # table rebuild. These triggers provide the same validation safely.
        connection.execute(text(
            "CREATE TRIGGER IF NOT EXISTS trg_academic_year_subject_kind_insert "
            "BEFORE INSERT ON academic_year_subjects FOR EACH ROW "
            "WHEN NEW.subject_kind IS NULL OR NEW.subject_kind NOT IN ('exam', 'behavior') "
            "BEGIN SELECT RAISE(ABORT, 'subject_kind must be exam or behavior'); END"
        ))
        connection.execute(text(
            "CREATE TRIGGER IF NOT EXISTS trg_academic_year_subject_kind_update "
            "BEFORE UPDATE OF subject_kind ON academic_year_subjects FOR EACH ROW "
            "WHEN NEW.subject_kind IS NULL OR NEW.subject_kind NOT IN ('exam', 'behavior') "
            "BEGIN SELECT RAISE(ABORT, 'subject_kind must be exam or behavior'); END"
        ))


def _record_migration(connection):
    connection.execute(text(
        "CREATE TABLE IF NOT EXISTS schema_migrations "
        "(version VARCHAR(120) PRIMARY KEY, applied_at TIMESTAMP NOT NULL)"
    ))
    dialect = connection.dialect.name
    if dialect == "sqlite":
        connection.execute(text(
            "INSERT OR IGNORE INTO schema_migrations "
            "(version, applied_at) VALUES (:version, CURRENT_TIMESTAMP)"
        ), {"version": VERSION})
    elif dialect == "postgresql":
        connection.execute(text(
            "INSERT INTO schema_migrations (version, applied_at) "
            "VALUES (:version, CURRENT_TIMESTAMP) ON CONFLICT (version) DO NOTHING"
        ), {"version": VERSION})
    else:
        connection.execute(text(
            "INSERT IGNORE INTO schema_migrations (version, applied_at) "
            "VALUES (:version, CURRENT_TIMESTAMP)"
        ), {"version": VERSION})


def upgrade():
    app = create_app()
    with app.app_context():
        with db.engine.begin() as connection:
            if not _ensure_subject_kind_column(connection):
                raise RuntimeError("academic_year_subjects table does not exist")
            _ensure_index(connection)
            _ensure_kind_guard(connection)
            _record_migration(connection)


def main():
    parser = argparse.ArgumentParser(
        description="Classify year-aware subjects as examination or behavior subjects"
    )
    parser.parse_args()
    upgrade()
    print(f"Applied {VERSION}; column: {TABLE}.subject_kind")


if __name__ == "__main__":
    main()
