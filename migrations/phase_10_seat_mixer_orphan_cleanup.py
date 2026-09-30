"""Remove Seat Mixer child records whose saved layout version no longer exists."""

import argparse
import os
import sys

from sqlalchemy import inspect, text

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import create_app, db


VERSION = "phase_10_seat_mixer_orphan_cleanup_v1"


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
            "INSERT IGNORE INTO schema_migrations (version, applied_at) "
            "VALUES (:version, CURRENT_TIMESTAMP)"
        ), {"version": VERSION})


def upgrade():
    app = create_app()
    with app.app_context():
        inspector = inspect(db.engine)
        required = {"exam_hall_versions", "seat_mixer_assignments", "seat_mixer_save_snapshots"}
        if not required.issubset(set(inspector.get_table_names())):
            raise RuntimeError("Seat Mixer tables do not exist")

        if inspector.has_table("schema_migrations"):
            applied = db.session.execute(
                text("SELECT 1 FROM schema_migrations WHERE version = :version"),
                {"version": VERSION},
            ).first()
            if applied:
                return {"status": "already_applied", "assignments": 0, "snapshots": 0, "settings": 0}

        with db.engine.begin() as connection:
            assignment_result = connection.execute(text(
                "DELETE FROM seat_mixer_assignments "
                "WHERE version_id NOT IN (SELECT id FROM exam_hall_versions)"
            ))
            snapshot_result = connection.execute(text(
                "DELETE FROM seat_mixer_save_snapshots "
                "WHERE version_id NOT IN (SELECT id FROM exam_hall_versions)"
            ))

            # Existing PostgreSQL/MySQL databases may predate the ORM cascade
            # constraints. Add them after the orphan rows are removed so future
            # version deletions cannot leave detached seating records behind.
            if db.engine.dialect.name != "sqlite":
                for table_name, constraint_name in (
                    ("seat_mixer_assignments", "fk_smixer_assignments_version"),
                    ("seat_mixer_save_snapshots", "fk_smixer_snapshots_version"),
                ):
                    foreign_keys = inspect(db.engine).get_foreign_keys(table_name)
                    covered = {
                        tuple(item.get("constrained_columns") or [])
                        for item in foreign_keys
                    }
                    if ("version_id",) not in covered:
                        connection.execute(text(
                            f"ALTER TABLE {table_name} ADD CONSTRAINT {constraint_name} "
                            "FOREIGN KEY (version_id) REFERENCES exam_hall_versions(id) ON DELETE CASCADE"
                        ))

            settings_removed = 0
            if inspector.has_table("settings"):
                settings = connection.execute(text(
                    "SELECT key FROM settings "
                    "WHERE key LIKE 'seat_mixer_current_snapshot_v1:%' "
                    "OR key LIKE 'seat_mixer_class_colors_v1:%'"
                )).mappings().all()
                valid_version_ids = {
                    str(row[0]) for row in connection.execute(
                        text("SELECT id FROM exam_hall_versions")
                    ).all()
                }
                for row in settings:
                    key = row["key"]
                    version_id = key.rsplit(":", 1)[-1]
                    if version_id not in valid_version_ids:
                        connection.execute(text("DELETE FROM settings WHERE key = :key"), {"key": key})
                        settings_removed += 1

            _record_migration(connection)

        return {
            "status": "applied",
            "assignments": max(assignment_result.rowcount or 0, 0),
            "snapshots": max(snapshot_result.rowcount or 0, 0),
            "settings": settings_removed,
        }


def main():
    argparse.ArgumentParser(description="Remove orphaned Seat Mixer child records").parse_args()
    result = upgrade()
    print(f"{VERSION}: {result['status']} "
          f"(assignments={result['assignments']}, snapshots={result['snapshots']}, settings={result['settings']})")


if __name__ == "__main__":
    main()
