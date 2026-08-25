"""Additive Phase 4A exam-aware Promotion Rules migration.

The migration keeps existing Phase 3B rules as legacy rows (``exam_id`` is
NULL), adds the explicit final-evaluation flag to Results ``exams``, and
allows one rule per Academic Year + Academic Year Level + Exam.

No results, enrollments, evaluations, or movement history are rewritten.
"""

import argparse
import os
import sys

from sqlalchemy import inspect, text

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import create_app, db


VERSION = "phase_4a_exam_aware_promotion_rules_v1"


def _postgres_constraints(connection, table_name):
    rows = connection.execute(
        text(
            "SELECT conname FROM pg_constraint "
            "WHERE conrelid = CAST(:table_name AS regclass)"
        ),
        {"table_name": table_name},
    )
    return {row[0] for row in rows}


def _add_column(connection, table_name, column_name, definition):
    columns = {row[1] for row in connection.execute(text(f"PRAGMA table_info({table_name})"))}
    if column_name not in columns:
        connection.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}"))


def _ensure_sqlite_columns(connection):
    # SQLite is used for local development. Fresh databases get the current
    # model shape from db.create_all; existing local databases still receive
    # the new columns without touching any application data.
    _add_column(connection, "exams", "is_final_evaluation", "BOOLEAN NOT NULL DEFAULT 0")
    _add_column(connection, "promotion_rules", "exam_id", "INTEGER")


def _sqlite_unique_index_columns(connection, table_name):
    """Return the ordered columns for every unique SQLite index on a table."""
    indexes = []
    for row in connection.execute(text(f"PRAGMA index_list({table_name})")):
        # PRAGMA index_list columns: seq, name, unique, origin, partial.
        if not row[2]:
            continue
        index_name = row[1]
        columns = [item[2] for item in connection.execute(text(f"PRAGMA index_info({index_name})"))]
        indexes.append(tuple(columns))
    return indexes


def _ensure_sqlite_promotion_rule_constraint(connection):
    """Rebuild the legacy SQLite table when its unique scope omits exam_id.

    SQLite cannot drop a table-level UNIQUE constraint in place. The rebuild
    copies every compatible row and preserves primary-key values so dependent
    critical-subject and evaluation rows keep their relationships.
    """
    table_exists = connection.execute(
        text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='promotion_rules'")
    ).first()
    replacement = "promotion_rules_phase4a_new"
    if not table_exists:
        # A process interruption after the old table was dropped can leave a
        # fully populated replacement behind. Recover it without losing IDs.
        replacement_exists = connection.execute(
            text(
                "SELECT 1 FROM sqlite_master "
                "WHERE type='table' AND name='promotion_rules_phase4a_new'"
            )
        ).first()
        if not replacement_exists:
            return False
        connection.execute(text(f"ALTER TABLE {replacement} RENAME TO promotion_rules"))
        return True

    expected_columns = {
        "id",
        "academic_year_id",
        "academic_year_level_id",
        "exam_id",
        "is_active",
        "overall_pass_threshold",
        "critical_subject_pass_threshold",
        "created_at",
        "updated_at",
    }
    columns = [row[1] for row in connection.execute(text("PRAGMA table_info(promotion_rules)"))]
    unknown_columns = set(columns) - expected_columns
    missing_columns = expected_columns - set(columns)
    if missing_columns:
        raise RuntimeError(
            "Phase 4A cannot rebuild promotion_rules; missing columns: "
            + ", ".join(sorted(missing_columns))
        )
    if unknown_columns:
        raise RuntimeError(
            "Phase 4A cannot rebuild promotion_rules without risking extra columns: "
            + ", ".join(sorted(unknown_columns))
        )

    correct_scope = ("academic_year_id", "academic_year_level_id", "exam_id")
    if correct_scope in _sqlite_unique_index_columns(connection, "promotion_rules"):
        return False

    connection.execute(text(f"DROP TABLE IF EXISTS {replacement}"))
    connection.execute(text(
        f"CREATE TABLE {replacement} ("
        "id INTEGER NOT NULL, "
        "academic_year_id INTEGER NOT NULL, "
        "academic_year_level_id INTEGER NOT NULL, "
        "exam_id INTEGER NULL, "
        "is_active BOOLEAN NOT NULL, "
        "overall_pass_threshold NUMERIC(6, 3) NOT NULL, "
        "critical_subject_pass_threshold NUMERIC(6, 3) NOT NULL, "
        "created_at DATETIME NOT NULL, "
        "updated_at DATETIME NOT NULL, "
        "PRIMARY KEY (id), "
        "CONSTRAINT uq_promotion_rule_year_level_exam UNIQUE "
        "(academic_year_id, academic_year_level_id, exam_id), "
        "CONSTRAINT ck_promotion_rule_overall_threshold CHECK "
        "(overall_pass_threshold >= 0 AND overall_pass_threshold <= 100), "
        "CONSTRAINT ck_promotion_rule_critical_threshold CHECK "
        "(critical_subject_pass_threshold >= 0 AND critical_subject_pass_threshold <= 100), "
        "FOREIGN KEY(academic_year_id) REFERENCES academic_years (id) ON DELETE CASCADE, "
        "FOREIGN KEY(academic_year_level_id) REFERENCES academic_year_levels (id) ON DELETE CASCADE, "
        "FOREIGN KEY(exam_id) REFERENCES exams (id) ON DELETE SET NULL"
        ")"
    ))
    column_sql = ", ".join(columns)
    connection.execute(text(
        f"INSERT INTO {replacement} ({column_sql}) "
        f"SELECT {column_sql} FROM promotion_rules"
    ))
    connection.execute(text("DROP TABLE promotion_rules"))
    connection.execute(text(f"ALTER TABLE {replacement} RENAME TO promotion_rules"))
    for index_name, column in {
        "ix_promotion_rules_academic_year_id": "academic_year_id",
        "ix_promotion_rules_academic_year_level_id": "academic_year_level_id",
        "ix_promotion_rules_exam_id": "exam_id",
    }.items():
        connection.execute(text(
            f"CREATE INDEX IF NOT EXISTS {index_name} ON promotion_rules ({column})"
        ))
    return True


def _record_migration(connection, dialect):
    connection.execute(
        text(
            "CREATE TABLE IF NOT EXISTS schema_migrations "
            "(version VARCHAR(120) PRIMARY KEY, applied_at TIMESTAMP NOT NULL)"
        )
    )
    if dialect == "sqlite":
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


def upgrade():
    app = create_app()
    with app.app_context():
        db.create_all()
        dialect = db.engine.dialect.name
        if dialect == "sqlite":
            # PRAGMA foreign_keys cannot be changed inside an open transaction.
            # Disable it before the transactional table swap, then always turn
            # it back on after the rebuild and migration ledger write.
            connection = db.engine.connect()
            connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
            connection.commit()
            try:
                with connection.begin():
                    _ensure_sqlite_columns(connection)
                    # Phase 4A was previously recorded after only adding
                    # exam_id; repair that actual legacy SQLite constraint.
                    _ensure_sqlite_promotion_rule_constraint(connection)
                    _record_migration(connection, dialect)
            finally:
                connection.exec_driver_sql("PRAGMA foreign_keys=ON")
                connection.commit()
                connection.close()
        elif dialect == "postgresql":
            with db.engine.begin() as connection:
                inspector = inspect(db.engine)
                exam_columns = {item["name"] for item in inspector.get_columns("exams")}
                if "is_final_evaluation" not in exam_columns:
                    connection.execute(
                        text(
                            "ALTER TABLE exams ADD COLUMN is_final_evaluation "
                            "BOOLEAN NOT NULL DEFAULT FALSE"
                        )
                    )
                rule_columns = {item["name"] for item in inspector.get_columns("promotion_rules")}
                if "exam_id" not in rule_columns:
                    connection.execute(
                        text("ALTER TABLE promotion_rules ADD COLUMN exam_id INTEGER")
                    )
                constraints = _postgres_constraints(connection, "promotion_rules")
                if "uq_promotion_rule_year_level" in constraints:
                    connection.execute(
                        text(
                            "ALTER TABLE promotion_rules DROP CONSTRAINT "
                            "uq_promotion_rule_year_level"
                        )
                    )
                constraints = _postgres_constraints(connection, "promotion_rules")
                if "uq_promotion_rule_year_level_exam" not in constraints:
                    connection.execute(
                        text(
                            "ALTER TABLE promotion_rules ADD CONSTRAINT "
                            "uq_promotion_rule_year_level_exam UNIQUE "
                            "(academic_year_id, academic_year_level_id, exam_id)"
                        )
                    )
                if "fk_promotion_rules_exam_id" not in constraints:
                    connection.execute(
                        text(
                            "ALTER TABLE promotion_rules ADD CONSTRAINT "
                            "fk_promotion_rules_exam_id FOREIGN KEY (exam_id) "
                            "REFERENCES exams(id) ON DELETE SET NULL"
                        )
                    )
                connection.execute(
                    text(
                        "CREATE INDEX IF NOT EXISTS idx_promotion_rules_exam_id "
                        "ON promotion_rules (exam_id)"
                    )
                )
                connection.execute(
                    text(
                        "CREATE INDEX IF NOT EXISTS idx_exams_final_evaluation "
                        "ON exams (is_final_evaluation)"
                    )
                )
                _record_migration(connection, dialect)
        else:
            raise RuntimeError(f"Unsupported Phase 4A database dialect: {dialect}")
        return VERSION


def main():
    argparse.ArgumentParser(
        description="Apply the Phase 4A exam-aware Promotion Rules migration"
    ).parse_args()
    print(f"Applied {upgrade()}")


if __name__ == "__main__":
    main()
