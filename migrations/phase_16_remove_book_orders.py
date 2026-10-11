"""Remove the retired Books ordering, payment, tracking, and printer portal data."""

import json
import os
import sys

from sqlalchemy import inspect, text

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import create_app, db


VERSION = "phase_16_remove_book_orders_v1"
ORDER_TABLES = ("book_order_events", "book_orders", "book_print_center_users")


def upgrade():
    app = create_app()
    with app.app_context():
        dropped_rows = {}
        permissions_cleaned = 0
        printer_only_accounts_disabled = 0
        with db.engine.begin() as connection:
            inspector = inspect(connection)
            existing_tables = set(inspector.get_table_names())

            for table in ORDER_TABLES:
                if table in existing_tables:
                    dropped_rows[table] = connection.execute(text(f'SELECT COUNT(*) FROM "{table}"')).scalar_one()
                    connection.execute(text(f'DROP TABLE "{table}"'))

            if "users" in existing_tables:
                user_columns = {column["name"] for column in inspector.get_columns("users")}
                if {"id", "permissions"}.issubset(user_columns):
                    teacher_user_ids = set()
                    if "teachers" in existing_tables:
                        teacher_columns = {column["name"] for column in inspector.get_columns("teachers")}
                        if "user_id" in teacher_columns:
                            teacher_user_ids = {
                                row[0] for row in connection.execute(
                                    text("SELECT user_id FROM teachers WHERE user_id IS NOT NULL")
                                )
                            }

                    selection = "SELECT id, permissions"
                    if "role" in user_columns:
                        selection += ", role"
                    if "is_active" in user_columns:
                        selection += ", is_active"
                    for row in connection.execute(text(f"{selection} FROM users")):
                        values = row._mapping
                        if values["id"] in teacher_user_ids:
                            continue
                        try:
                            permissions = json.loads(values["permissions"] or "[]")
                        except (TypeError, ValueError):
                            continue
                        if not isinstance(permissions, list) or "books_printer" not in permissions:
                            continue

                        remaining = sorted({value for value in permissions if isinstance(value, str) and value != "books_printer"})
                        connection.execute(
                            text("UPDATE users SET permissions = :permissions WHERE id = :user_id"),
                            {"permissions": json.dumps(remaining), "user_id": values["id"]},
                        )
                        permissions_cleaned += 1

                        if (
                            not remaining
                            and values.get("role") == "staff"
                            and "is_active" in user_columns
                            and values.get("is_active")
                        ):
                            connection.execute(
                                text("UPDATE users SET is_active = :active WHERE id = :user_id"),
                                {"active": False, "user_id": values["id"]},
                            )
                            printer_only_accounts_disabled += 1

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

        return {
            "status": "applied",
            "deleted_rows": dropped_rows,
            "printer_permissions_removed": permissions_cleaned,
            "printer_only_accounts_disabled": printer_only_accounts_disabled,
        }


if __name__ == "__main__":
    print(upgrade())
