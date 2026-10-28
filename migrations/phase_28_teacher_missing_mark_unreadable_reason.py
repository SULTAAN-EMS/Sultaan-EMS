"""Allow a separate missing-mark reason for unreadable exam papers."""

import argparse
import os
import sys

from sqlalchemy import inspect, text
from sqlalchemy.schema import CreateIndex, CreateTable

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import create_app, db
from app.models import TeacherResultMissingMarkReport


VERSION = "phase_28_teacher_missing_mark_unreadable_reason_v1"
TABLE = "teacher_result_missing_mark_reports"
CONSTRAINT = "ck_teacher_result_missing_report_reason"


def _record_version(connection):
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


def _sqlite_rebuild_reason_constraint(connection):
    inspector = inspect(connection)
    for table_name in inspector.get_table_names():
        if table_name == TABLE:
            continue
        if any(fk.get("referred_table") == TABLE for fk in inspector.get_foreign_keys(table_name)):
            raise RuntimeError(f"Cannot rebuild {TABLE}: another table has a foreign key to it")

    temp_name = f"{TABLE}_phase28"
    connection.execute(text(f'DROP TABLE IF EXISTS "{temp_name}"'))
    create_sql = str(CreateTable(TeacherResultMissingMarkReport.__table__).compile(
        dialect=connection.dialect,
    ))
    create_sql = create_sql.replace(
        f"CREATE TABLE {TABLE} (",
        f"CREATE TABLE {temp_name} (",
        1,
    )
    if create_sql == str(CreateTable(TeacherResultMissingMarkReport.__table__).compile(
        dialect=connection.dialect,
    )):
        raise RuntimeError("Could not prepare the temporary report table")
    connection.execute(text(create_sql))

    column_names = [column["name"] for column in inspector.get_columns(TABLE)]
    columns_sql = ", ".join(f'"{name}"' for name in column_names)
    connection.execute(text(
        f'INSERT INTO "{temp_name}" ({columns_sql}) '
        f'SELECT {columns_sql} FROM "{TABLE}"'
    ))
    connection.execute(text(f'DROP TABLE "{TABLE}"'))
    connection.execute(text(f'ALTER TABLE "{temp_name}" RENAME TO "{TABLE}"'))
    for index in TeacherResultMissingMarkReport.__table__.indexes:
        connection.execute(CreateIndex(index))


def upgrade():
    app = create_app()
    with app.app_context():
        inspector = inspect(db.engine)
        if TABLE not in inspector.get_table_names():
            raise RuntimeError(f"Required table is missing before migration: {TABLE}")

        with db.engine.begin() as connection:
            if connection.dialect.name == "postgresql":
                connection.execute(text(
                    "SELECT pg_advisory_xact_lock(hashtext(:lock_name)::bigint)"
                ), {"lock_name": VERSION})
            connection.execute(text(
                "CREATE TABLE IF NOT EXISTS schema_migrations "
                "(version VARCHAR(120) PRIMARY KEY, applied_at TIMESTAMP NOT NULL)"
            ))
            if connection.execute(
                text("SELECT 1 FROM schema_migrations WHERE version = :version"),
                {"version": VERSION},
            ).first():
                return {"status": "already_applied"}

            checks = inspect(connection).get_check_constraints(TABLE)
            reason_checks = [
                item for item in checks
                if "reason" in (item.get("sqltext") or "").lower()
            ]
            already_updated = any(
                "paper_unreadable" in (item.get("sqltext") or "").lower()
                for item in reason_checks
            )
            if not already_updated:
                dialect = connection.dialect.name
                if dialect == "sqlite":
                    _sqlite_rebuild_reason_constraint(connection)
                else:
                    for item in reason_checks:
                        name = item.get("name")
                        if not name:
                            raise RuntimeError("The existing reason constraint has no name")
                        if dialect == "mysql":
                            connection.execute(text(f"ALTER TABLE {TABLE} DROP CHECK {name}"))
                        else:
                            connection.execute(text(f"ALTER TABLE {TABLE} DROP CONSTRAINT {name}"))
                    connection.execute(text(
                        f"ALTER TABLE {TABLE} ADD CONSTRAINT {CONSTRAINT} "
                        "CHECK (reason IN ('absent', 'paper_lost', 'paper_damaged', "
                        "'paper_unreadable', 'other'))"
                    ))
            _record_version(connection)
        return {"status": "applied", "table": TABLE}


if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__).parse_args()
    print(upgrade())
