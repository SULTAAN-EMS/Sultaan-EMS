"""Controlled additive migration for the Phase 3B promotion foundation.

This migration creates only the promotion-rule configuration and evaluation
snapshot tables. It never rewrites results, enrollments, or existing reports.
"""

import argparse
import os
import sys

from sqlalchemy import text

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import create_app, db
from app.models import PromotionEvaluation, PromotionRule, PromotionRuleCriticalSubject


VERSION = "phase_3b_promotion_rules_v1"
TABLES = (PromotionRule, PromotionRuleCriticalSubject, PromotionEvaluation)


def _ensure_ledger(connection, dialect_name):
    connection.execute(text(
        "CREATE TABLE IF NOT EXISTS schema_migrations "
        "(version VARCHAR(120) PRIMARY KEY, applied_at TIMESTAMP NOT NULL)"
    ))
    if dialect_name == "sqlite":
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
    """Create Phase 3B tables and record the idempotent migration version."""
    app = create_app()
    with app.app_context():
        engine = db.engine
        for model in TABLES:
            model.__table__.create(bind=engine, checkfirst=True)
        with engine.begin() as connection:
            _ensure_ledger(connection, engine.dialect.name)
        return [model.__tablename__ for model in TABLES]


def main():
    parser = argparse.ArgumentParser(description="Apply the Phase 3B promotion rules foundation")
    parser.parse_args()
    tables = upgrade()
    print(f"Applied {VERSION}; tables: {', '.join(tables)}")


if __name__ == "__main__":
    main()
