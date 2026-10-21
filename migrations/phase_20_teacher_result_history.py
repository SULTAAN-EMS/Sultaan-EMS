"""Create a durable event journal for teacher result-entry workflows."""

import argparse
import os
import sys

from sqlalchemy import inspect, text

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import create_app, db
from app.models import TeacherResultEvent


VERSION = "phase_20_teacher_result_history_v1"


def upgrade():
    app = create_app()
    with app.app_context():
        required = {
            "teacher_result_assignments", "teacher_result_submissions", "teacher_result_marks",
            "teachers", "users", "academic_years", "exams", "academic_classes",
            "academic_levels", "subjects",
        }
        missing = required - set(inspect(db.engine).get_table_names())
        if missing:
            raise RuntimeError(f"Required tables are missing before migration: {', '.join(sorted(missing))}")

        TeacherResultEvent.__table__.create(bind=db.engine, checkfirst=True)
        with db.engine.begin() as connection:
            connection.execute(text(
                "CREATE TABLE IF NOT EXISTS schema_migrations "
                "(version VARCHAR(120) PRIMARY KEY, applied_at TIMESTAMP NOT NULL)"
            ))
            applied = connection.execute(
                text("SELECT 1 FROM schema_migrations WHERE version = :version"),
                {"version": VERSION},
            ).first()
            if not applied:
                connection.execute(text("""
                    INSERT INTO teacher_result_events (
                        assignment_id, submission_id, teacher_id, actor_user_id,
                        event_type, status_from, status_to, marks_count, note,
                        created_at, teacher_name, teacher_code, actor_name,
                        academic_year_name, exam_name, level_name, class_name, subject_name
                    )
                    SELECT
                        a.id, s.id, t.id, s.reviewed_by_id,
                        'baseline', NULL, s.status,
                        (SELECT COUNT(*) FROM teacher_result_marks m
                         WHERE m.submission_id = s.id AND m.score IS NOT NULL),
                        'Xaaladda jirtay markii taariikhda natiijo-gelinta la hirgeliyey; tallaabooyinkii hore lama kaydin jirin.',
                        COALESCE(s.updated_at, s.created_at),
                        t.full_name, t.teacher_code,
                        COALESCE(u.full_name, u.username),
                        y.name, e.name,
                        COALESCE(yl.name, al.name, ''),
                        COALESCE(yc.name, c.name),
                        COALESCE(ys.name, sub.name)
                    FROM teacher_result_submissions s
                    JOIN teacher_result_assignments a ON a.id = s.assignment_id
                    JOIN teachers t ON t.id = a.teacher_id
                    JOIN academic_years y ON y.id = a.academic_year_id
                    JOIN exams e ON e.id = a.exam_id
                    JOIN academic_classes c ON c.id = a.class_id
                    JOIN subjects sub ON sub.id = a.subject_id
                    LEFT JOIN academic_levels al ON al.id = c.academic_level_id
                    LEFT JOIN users u ON u.id = s.reviewed_by_id
                    LEFT JOIN academic_year_levels yl
                      ON yl.academic_year_id = a.academic_year_id
                     AND yl.legacy_level_id = c.academic_level_id
                    LEFT JOIN academic_year_classes yc
                      ON yc.academic_year_level_id = yl.id
                     AND yc.legacy_class_id = c.id
                    LEFT JOIN academic_year_subjects ys
                      ON ys.academic_year_id = a.academic_year_id
                     AND ys.academic_year_level_id = yl.id
                     AND ys.legacy_subject_id = sub.id
                     AND ys.subject_kind = 'exam'
                """))
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

        return {"status": "applied", "table": "teacher_result_events"}


if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__).parse_args()
    print(upgrade())
