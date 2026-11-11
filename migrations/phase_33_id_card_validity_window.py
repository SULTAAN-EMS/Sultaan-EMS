"""Store exact EAT validity windows for student ID cards."""

import argparse

from sqlalchemy import inspect, text

from app import create_app, db


VERSION = "phase_33_id_card_validity_window_v1"
COLUMNS = {
    "valid_from_at": "TIMESTAMP NULL",
    "expires_at": "TIMESTAMP NULL",
}


def ensure_columns(connection):
    inspector = inspect(connection)
    if not inspector.has_table("id_card_issues"):
        raise RuntimeError("id_card_issues table does not exist")
    existing = {
        column["name"] for column in inspector.get_columns("id_card_issues")
    }
    for name, sql_type in COLUMNS.items():
        if name not in existing:
            connection.execute(text(
                f"ALTER TABLE id_card_issues ADD COLUMN {name} {sql_type}"
            ))
            existing.add(name)


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
            ensure_columns(connection)

            if connection.dialect.name == "sqlite":
                statement = (
                    "INSERT OR IGNORE INTO schema_migrations (version, applied_at) "
                    "VALUES (:version, CURRENT_TIMESTAMP)"
                )
            elif connection.dialect.name == "postgresql":
                statement = (
                    "INSERT INTO schema_migrations (version, applied_at) "
                    "VALUES (:version, CURRENT_TIMESTAMP) ON CONFLICT (version) DO NOTHING"
                )
            else:
                statement = (
                    "INSERT IGNORE INTO schema_migrations (version, applied_at) "
                    "VALUES (:version, CURRENT_TIMESTAMP)"
                )
            connection.execute(text(statement), {"version": VERSION})

    return {"status": "applied", "version": VERSION, "columns": COLUMNS}


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    result = upgrade()
    print(f"{VERSION}: {result['status']}")


if __name__ == "__main__":
    main()
