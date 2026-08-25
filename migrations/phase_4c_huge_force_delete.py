"""Make the legacy Student year snapshot nullable for final archived purges.

Student identity is permanent; ``StudentEnrollment`` stores the authoritative
year history.  This additive compatibility migration lets a student survive a
purge when no later enrollment exists, without inventing a replacement year.
"""

import argparse
import os
import sys

from sqlalchemy import inspect, text
from sqlalchemy.schema import CreateIndex, CreateTable

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import create_app, db
from app.models import Student


VERSION = "phase_4c_huge_force_delete_v1"


def _sqlite_make_nullable(connection):
    inspector = inspect(connection)
    column = next(
        item for item in inspector.get_columns("students")
        if item["name"] == "academic_year_id"
    )
    if column.get("nullable", True):
        return

    old_table = "students__phase_4c_old"
    index_names = [item["name"] for item in inspector.get_indexes("students")]
    connection.execute(text("PRAGMA foreign_keys=OFF"))
    connection.execute(text(f"ALTER TABLE students RENAME TO {old_table}"))

    students_table = Student.__table__
    academic_year_column = students_table.c.academic_year_id
    previous_nullable = academic_year_column.nullable
    academic_year_column.nullable = True
    try:
        ddl = CreateTable(students_table).compile(dialect=connection.dialect)
    finally:
        academic_year_column.nullable = previous_nullable
    connection.exec_driver_sql(str(ddl))
    columns = ", ".join(column.name for column in Student.__table__.columns)
    connection.exec_driver_sql(
        f"INSERT INTO students ({columns}) SELECT {columns} FROM {old_table}"
    )
    connection.exec_driver_sql(f"DROP TABLE {old_table}")
    existing_indexes = {item["name"] for item in inspect(connection).get_indexes("students")}
    for index in Student.__table__.indexes:
        if index.name in index_names and index.name not in existing_indexes:
            connection.exec_driver_sql(
                str(CreateIndex(index).compile(dialect=connection.dialect))
            )
    connection.execute(text("PRAGMA foreign_keys=ON"))


def upgrade():
    app = create_app()
    with app.app_context():
        db.create_all()
        with db.engine.begin() as connection:
            inspector = inspect(connection)
            if inspector.has_table("students"):
                column = next(
                    item for item in inspector.get_columns("students")
                    if item["name"] == "academic_year_id"
                )
                if not column.get("nullable", True):
                    if db.engine.dialect.name == "postgresql":
                        connection.execute(
                            text(
                                "ALTER TABLE students "
                                "ALTER COLUMN academic_year_id DROP NOT NULL"
                            )
                        )
                    elif db.engine.dialect.name == "sqlite":
                        _sqlite_make_nullable(connection)
                    else:
                        raise RuntimeError(
                            f"Unsupported Phase 4C database dialect: {db.engine.dialect.name}"
                        )

            connection.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS schema_migrations "
                    "(version VARCHAR(120) PRIMARY KEY, applied_at TIMESTAMP NOT NULL)"
                )
            )
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
        return VERSION


def main():
    argparse.ArgumentParser(
        description="Apply the Phase 4C Student snapshot compatibility migration"
    ).parse_args()
    print(f"Applied {upgrade()}")


if __name__ == "__main__":
    main()
