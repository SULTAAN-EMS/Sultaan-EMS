"""Bind generated ID-card QR codes to one academic-year examination."""

import argparse
import os
import sys

from sqlalchemy import inspect, text

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import create_app, db


VERSION = "phase_9_exam_scoped_id_cards_v1"


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
        if not inspector.has_table("id_card_issues"):
            raise RuntimeError("id_card_issues table does not exist")

        existing_migration = None
        if inspector.has_table("schema_migrations"):
            existing_migration = db.session.execute(
                text("SELECT 1 FROM schema_migrations WHERE version = :version"),
                {"version": VERSION},
            ).first()
        if existing_migration:
            return {"status": "already_applied"}

        columns = {column["name"] for column in inspector.get_columns("id_card_issues")}
        dialect = db.engine.dialect.name
        with db.engine.begin() as connection:
            if "exam_id" not in columns:
                connection.execute(text("ALTER TABLE id_card_issues ADD COLUMN exam_id INTEGER NULL"))

            indexes = inspector.get_indexes("id_card_issues")
            constraints = inspector.get_unique_constraints("id_card_issues")
            names = {item.get("name") for item in indexes + constraints}
            if "uq_id_card_student_year_exam" not in names:
                if dialect == "postgresql" or dialect == "sqlite":
                    connection.execute(text(
                        "CREATE UNIQUE INDEX IF NOT EXISTS uq_id_card_student_year_exam "
                        "ON id_card_issues (student_id, academic_year_id, exam_id)"
                    ))
                else:
                    connection.execute(text(
                        "CREATE UNIQUE INDEX uq_id_card_student_year_exam "
                        "ON id_card_issues (student_id, academic_year_id, exam_id)"
                    ))

            if dialect != "sqlite":
                foreign_keys = inspector.get_foreign_keys("id_card_issues")
                covered = {tuple(item.get("constrained_columns") or []) for item in foreign_keys}
                if ("exam_id",) not in covered:
                    connection.execute(text(
                        "ALTER TABLE id_card_issues ADD CONSTRAINT fk_id_card_issues_exam_id "
                        "FOREIGN KEY (exam_id) REFERENCES exams(id) ON DELETE SET NULL"
                    ))

            _record_migration(connection)

        return {"status": "applied"}


def main():
    argparse.ArgumentParser(description="Bind ID-card QR codes to an exam scope").parse_args()
    result = upgrade()
    print(f"{VERSION}: {result['status']}")


if __name__ == "__main__":
    main()
