"""Add branch-aware book catalogs and print-center contact records."""

import os
import sys

from sqlalchemy import inspect, text

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import create_app, db
from app.models import (
    BookBranch,
    BookLibraryItem,
    BookPrintCenter,
    book_branch_print_centers,
    book_item_branches,
)


VERSION = "phase_15_books_branches_contacts_v1"


def upgrade():
    app = create_app()
    with app.app_context():
        with db.engine.begin() as connection:
            inspector = inspect(connection)
            if not inspector.has_table("book_library_items"):
                raise RuntimeError("Run phase_14_books_library.py before phase_15_books_branches_orders.py")
            columns = {column["name"] for column in inspector.get_columns("book_library_items")}
            if "catalog_type" not in columns:
                connection.execute(text(
                    "ALTER TABLE book_library_items ADD COLUMN catalog_type "
                    "VARCHAR(20) NOT NULL DEFAULT 'unassigned'"
                ))
            connection.execute(text(
                "UPDATE book_library_items SET catalog_type = 'unassigned' "
                "WHERE catalog_type IS NULL OR catalog_type NOT IN ('national', 'local', 'unassigned')"
            ))

        db.metadata.create_all(bind=db.engine, tables=[
            BookBranch.__table__,
            BookPrintCenter.__table__,
            book_item_branches,
            book_branch_print_centers,
        ])
        db.Index("ix_book_library_items_catalog_type", BookLibraryItem.catalog_type).create(bind=db.engine, checkfirst=True)

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
        return {"status": "applied", "tables": [
            "book_branches", "book_print_centers", "book_item_branches", "book_branch_print_centers",
        ]}


if __name__ == "__main__":
    print(upgrade())
