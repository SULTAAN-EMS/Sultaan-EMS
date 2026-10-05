"""Add session-scoped official school closure attendance records."""

import os
import sys

from sqlalchemy import Index, inspect, text

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import create_app, db
from app.models import BehaviorAttendanceClosure, BehaviorAttendanceRecord


VERSION = "phase_13_behavior_attendance_closures_v1"


def upgrade():
    app = create_app()
    with app.app_context():
        inspector = inspect(db.engine)
        tables = set(inspector.get_table_names())
        required = {"behavior_attendance_records", "behavior_sessions", "behavior_configurations", "academic_years"}
        missing = required - tables
        if missing:
            raise RuntimeError(f"Required tables are missing before migration: {', '.join(sorted(missing))}")

        BehaviorAttendanceClosure.__table__.create(bind=db.engine, checkfirst=True)
        columns = {item["name"] for item in inspect(db.engine).get_columns("behavior_attendance_records")}
        if "behavior_attendance_closure_id" not in columns:
            dialect = db.engine.dialect.name
            if dialect == "sqlite":
                statement = (
                    "ALTER TABLE behavior_attendance_records ADD COLUMN "
                    "behavior_attendance_closure_id INTEGER REFERENCES behavior_attendance_closures(id) ON DELETE SET NULL"
                )
            elif dialect == "postgresql":
                statement = (
                    "ALTER TABLE behavior_attendance_records ADD COLUMN "
                    "behavior_attendance_closure_id INTEGER REFERENCES behavior_attendance_closures(id) ON DELETE SET NULL"
                )
            else:
                statement = (
                    "ALTER TABLE behavior_attendance_records ADD COLUMN "
                    "behavior_attendance_closure_id INTEGER NULL, "
                    "ADD CONSTRAINT fk_behavior_attendance_closure "
                    "FOREIGN KEY (behavior_attendance_closure_id) "
                    "REFERENCES behavior_attendance_closures(id) ON DELETE SET NULL"
                )
            with db.engine.begin() as connection:
                connection.execute(text(statement))
        Index(
            "ix_behavior_attendance_records_closure_id",
            BehaviorAttendanceRecord.behavior_attendance_closure_id,
        ).create(bind=db.engine, checkfirst=True)

        with db.engine.begin() as connection:
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
        return {"status": "applied", "tables": 1, "columns": 1}


if __name__ == "__main__":
    print(upgrade())
