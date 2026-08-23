"""Controlled Phase 1D migration for the academic year hierarchy.

This migration is intentionally separate from ``schema_compat.py``.  Run it
explicitly in the target environment after a verified backup.  It only creates
the three new tables and performs conservative local backfill; it never drops
or rewrites legacy hierarchy/data records.
"""

import argparse
import json
import os
import sys
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import create_app, db
from app.academic_hierarchy import backfill_year_hierarchy
from app.models import AcademicYearClass, AcademicYearLevel, AcademicYearSubject


VERSION = "phase_1d_academic_hierarchy_v1"
TABLES = (AcademicYearLevel, AcademicYearClass, AcademicYearSubject)


def upgrade(report_path=None, perform_backfill=True):
    """Create Phase 1D tables and optionally run conservative backfill."""
    app = create_app()
    with app.app_context():
        engine = db.engine
        for model in TABLES:
            model.__table__.create(bind=engine, checkfirst=True)

        # The repository has no Alembic/Flask-Migrate registry. Keep a tiny
        # explicit version ledger so this controlled migration is auditable
        # without adding startup-time schema mutation.
        with engine.begin() as connection:
            connection.execute(text(
                "CREATE TABLE IF NOT EXISTS schema_migrations "
                "(version VARCHAR(120) PRIMARY KEY, applied_at TIMESTAMP NOT NULL)"
            ))
            if engine.dialect.name == "sqlite":
                connection.execute(
                    text("INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (:version, CURRENT_TIMESTAMP)"),
                    {"version": VERSION},
                )
            else:
                connection.execute(
                    text("INSERT INTO schema_migrations (version, applied_at) VALUES (:version, CURRENT_TIMESTAMP) ON CONFLICT (version) DO NOTHING"),
                    {"version": VERSION},
                )

        report = None
        if perform_backfill:
            report = backfill_year_hierarchy(report_path=report_path)
        return report


def main():
    parser = argparse.ArgumentParser(description="Apply the local Phase 1D migration")
    parser.add_argument(
        "--report",
        default=str(Path(__file__).parent / "reports" / "phase_1d_backfill_exceptions.json"),
        help="Path for the conservative backfill/exception report",
    )
    parser.add_argument("--no-backfill", action="store_true", help="Create tables without data mapping")
    args = parser.parse_args()
    report = upgrade(report_path=args.report, perform_backfill=not args.no_backfill)
    print(f"Applied {VERSION}; tables: {', '.join(model.__tablename__ for model in TABLES)}")
    if report is not None:
        print(json.dumps({
            "mapped_levels": len(report["mapped_levels"]),
            "mapped_classes": len(report["mapped_classes"]),
            "mapped_subjects": len(report["mapped_subjects"]),
            "ambiguous": len(report["ambiguous"]),
            "cross_level_results": len(report["cross_level_results"]),
        }, sort_keys=True))


if __name__ == "__main__":
    main()
