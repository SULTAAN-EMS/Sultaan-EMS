"""Keep old Student IDs resolving after an identity edit."""

import argparse
import os
import sys

from sqlalchemy import inspect, text

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import create_app, db


VERSION = "phase_6_student_code_aliases_v1"


def upgrade():
    app = create_app()
    with app.app_context():
        inspector = inspect(db.engine)
        if not inspector.has_table("student_code_aliases"):
            dialect = db.engine.dialect.name
            if dialect == "postgresql":
                ddl = """
                    CREATE TABLE student_code_aliases (
                        id SERIAL PRIMARY KEY,
                        student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
                        old_code VARCHAR(50) NOT NULL UNIQUE,
                        new_code VARCHAR(50) NOT NULL,
                        changed_by_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )
                """
            elif dialect == "mysql":
                ddl = """
                    CREATE TABLE student_code_aliases (
                        id INTEGER NOT NULL AUTO_INCREMENT PRIMARY KEY,
                        student_id INTEGER NOT NULL,
                        old_code VARCHAR(50) NOT NULL UNIQUE,
                        new_code VARCHAR(50) NOT NULL,
                        changed_by_id INTEGER NULL,
                        created_at DATETIME NOT NULL,
                        updated_at DATETIME NOT NULL,
                        CONSTRAINT fk_student_code_aliases_student
                            FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE CASCADE,
                        CONSTRAINT fk_student_code_aliases_changed_by
                            FOREIGN KEY (changed_by_id) REFERENCES users(id) ON DELETE SET NULL
                    )
                """
            else:
                ddl = """
                    CREATE TABLE student_code_aliases (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
                        old_code VARCHAR(50) NOT NULL UNIQUE,
                        new_code VARCHAR(50) NOT NULL,
                        changed_by_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )
                """
            with db.engine.begin() as connection:
                connection.execute(text(ddl))

        with db.engine.begin() as connection:
            connection.execute(text(
                "CREATE TABLE IF NOT EXISTS schema_migrations "
                "(version VARCHAR(120) PRIMARY KEY, applied_at TIMESTAMP NOT NULL)"
            ))
            if connection.dialect.name == "sqlite":
                connection.execute(text(
                    "INSERT OR IGNORE INTO schema_migrations "
                    "(version, applied_at) VALUES (:version, CURRENT_TIMESTAMP)"
                ), {"version": VERSION})
            elif connection.dialect.name == "postgresql":
                connection.execute(text(
                    "INSERT INTO schema_migrations (version, applied_at) "
                    "VALUES (:version, CURRENT_TIMESTAMP) ON CONFLICT (version) DO NOTHING"
                ), {"version": VERSION})
            else:
                connection.execute(text(
                    "INSERT IGNORE INTO schema_migrations "
                    "(version, applied_at) VALUES (:version, CURRENT_TIMESTAMP)"
                ), {"version": VERSION})


def main():
    argparse.ArgumentParser(description="Add historical Student ID aliases").parse_args()
    upgrade()
    print(f"Applied {VERSION}; existing student identities were preserved")


if __name__ == "__main__":
    main()
