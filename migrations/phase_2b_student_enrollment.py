"""Controlled Phase 2B migration for the StudentEnrollment foundation.

This migration is additive and must be run explicitly in the verified staging
environment. It does not change legacy Student placement fields or perform a
production data cutover.
"""

import argparse
import json
import os
import sys
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import create_app, db
from app.enrollment_service import backfill_ready_students, dry_run_legacy_backfill
from app.models import StudentEnrollment


VERSION = "phase_2b_student_enrollment_v1"


def _ensure_ledger(connection, dialect_name):
    connection.execute(text(
        "CREATE TABLE IF NOT EXISTS schema_migrations "
        "(version VARCHAR(120) PRIMARY KEY, applied_at TIMESTAMP NOT NULL)"
    ))
    if dialect_name == "sqlite":
        connection.execute(
            text("INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (:version, CURRENT_TIMESTAMP)"),
            {"version": VERSION},
        )
    else:
        connection.execute(
            text("INSERT INTO schema_migrations (version, applied_at) VALUES (:version, CURRENT_TIMESTAMP) ON CONFLICT (version) DO NOTHING"),
            {"version": VERSION},
        )


def upgrade(report_path=None, perform_backfill=False):
    """Create the enrollment table and optionally backfill ready mappings."""
    app = create_app()
    with app.app_context():
        engine = db.engine
        StudentEnrollment.__table__.create(bind=engine, checkfirst=True)
        with engine.begin() as connection:
            _ensure_ledger(connection, engine.dialect.name)

        report = dry_run_legacy_backfill(report_path=report_path)
        backfill = {"backfilled_student_ids": [], "excluded_student_ids": []}
        if perform_backfill:
            backfill = backfill_ready_students(report)
        report["backfill_result"] = backfill
        return report


def main():
    parser = argparse.ArgumentParser(description="Apply the local Phase 2B StudentEnrollment migration")
    parser.add_argument(
        "--report",
        default=str(Path(__file__).parent / "reports" / "phase_2b_student_enrollment_backfill.json"),
        help="Path for the dry-run backfill/exception report",
    )
    parser.add_argument(
        "--backfill",
        action="store_true",
        help="Backfill only READY_TO_BACKFILL students after generating the report",
    )
    args = parser.parse_args()
    report = upgrade(report_path=args.report, perform_backfill=args.backfill)
    summary = report["summary"]
    print(f"Applied {VERSION}; table: {StudentEnrollment.__tablename__}")
    print(json.dumps({
        "total_students": summary["total_students"],
        "ready_to_backfill": summary["ready_to_backfill"],
        "ambiguous": summary["ambiguous"],
        "invalid": summary["invalid"],
        "excluded": len(report["backfill_result"]["excluded_student_ids"]),
        "backfilled": len(report["backfill_result"]["backfilled_student_ids"]),
    }, sort_keys=True))


if __name__ == "__main__":
    main()
