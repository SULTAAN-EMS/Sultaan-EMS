"""Add explicit school-stage classification to academic-year levels."""

import argparse
import os
import sys

from sqlalchemy import inspect, text

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import create_app, db


VERSION = "phase_5_student_school_stages_v1"


def infer_school_stage(level_name):
    normalized = " ".join(str(level_name or "").strip().lower().replace("-", " ").split())
    if "kindergarten" in normalized or normalized in {"kg", "xanaan", "xannaano", "xanaano"}:
        return "kindergarten"
    if "secondary" in normalized or "dugsi sare" in normalized or normalized == "sare":
        return "secondary"
    if "lower primary" in normalized or "dugsi hoose" in normalized or "hoose" in normalized:
        return "lower_primary"
    if (
        "upper primary" in normalized
        or "primary" in normalized
        or "middle" in normalized
        or "dugsi dhexe" in normalized
        or "dhexe" in normalized
    ):
        return "upper_primary"
    return None


def upgrade():
    app = create_app()
    with app.app_context():
        inspector = inspect(db.engine)
        if not inspector.has_table("academic_year_levels"):
            raise RuntimeError("academic_year_levels table does not exist")

        columns = {column["name"] for column in inspector.get_columns("academic_year_levels")}
        if "school_stage" not in columns:
            with db.engine.begin() as connection:
                connection.execute(text(
                    "ALTER TABLE academic_year_levels ADD COLUMN school_stage VARCHAR(30)"
                ))

        with db.engine.begin() as connection:
            rows = connection.execute(text(
                "SELECT id, name, school_stage FROM academic_year_levels"
            )).mappings().all()
            for row in rows:
                if row["school_stage"]:
                    continue
                stage = infer_school_stage(row["name"])
                if stage:
                    connection.execute(
                        text(
                            "UPDATE academic_year_levels "
                            "SET school_stage = :stage WHERE id = :id"
                        ),
                        {"stage": stage, "id": row["id"]},
                    )

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
                    "INSERT IGNORE INTO schema_migrations (version, applied_at) "
                    "VALUES (:version, CURRENT_TIMESTAMP)"
                ), {"version": VERSION})


def main():
    argparse.ArgumentParser(
        description="Add school-stage classification to academic-year levels"
    ).parse_args()
    upgrade()
    print(f"Applied {VERSION}; existing academic-year levels were preserved")


if __name__ == "__main__":
    main()
