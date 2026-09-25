"""Dependency-aware destructive deletion services.

The normal Configuration Center lifecycle is intentionally kept in
``routes_admin``.  This module owns the separate, final-stage purge so its
dependency graph and transaction boundary cannot be confused with a normal
delete.
"""

from pathlib import Path

from sqlalchemy import Table, inspect, or_, select, func
from flask import current_app

from . import db
from .models import (
    AcademicYear,
    AcademicYearClass,
    AcademicYearLevel,
    AcademicYearSubject,
    AttendanceRecord,
    Exam,
    ExamHall,
    ExamHallEnrollment,
    ExamHallSubject,
    ExamHallVersion,
    ExamMarkingConfiguration,
    ExamSession,
    ExamSessionSubject,
    ExamType,
    GradeScale,
    IdCardIssue,
    IncidentAttachment,
    IncidentReport,
    IncidentReportCategory,
    PromotionEvaluation,
    PromotionOutcomeApplication,
    PromotionRule,
    PromotionRuleCriticalSubject,
    ReportVerification,
    Result,
    SeatAssignment,
    SeatMixerAssignment,
    SeatMixerSaveSnapshot,
    Student,
    StudentComplaint,
    StudentComplaintReply,
    StudentEnrollment,
    StudentEnrollmentMovement,
    StudentFeedback,
    StudentFeedbackReply,
    BehaviorConfiguration,
    BehaviorAttendanceRecord,
)


class PurgeValidationError(ValueError):
    """A purge cannot safely proceed with the current data or authorization."""


STUDENT_DELETE_CONFIRMATION = "TIRTIR ARDEYGAN"
_STUDENT_PURGE_EXCLUDED_TABLES = {"audit_logs", "students", "student_enrollments", "behavior_attendance_deletions"}
_STUDENT_PURGE_LINK_COLUMNS = (
    "student_id",
    "student_enrollment_id",
    "enrollment_id",
    "source_enrollment_id",
    "destination_enrollment_id",
    "movement_id",
)


def _student_purge_unknown_dependencies(student_id):
    """Find direct student FKs that are not represented in the ORM metadata."""
    inspector = inspect(db.engine)
    known_tables = set(db.metadata.tables)
    unknown = []
    for table_name in inspector.get_table_names():
        if table_name not in known_tables:
            foreign_keys = inspector.get_foreign_keys(table_name)
            for foreign_key in foreign_keys:
                referred = foreign_key.get("referred_table")
                referred_columns = foreign_key.get("referred_columns") or []
                if referred not in {"students", "student_enrollments", "student_enrollment_movements"}:
                    continue
                local_columns = foreign_key.get("constrained_columns") or []
                if not local_columns or not referred_columns:
                    continue
                table = Table(table_name, db.metadata, autoload_with=db.engine)
                for local_column, referred_column in zip(local_columns, referred_columns):
                    if referred_column != "id" or local_column not in table.c:
                        continue
                    count = db.session.execute(
                        select(func.count()).select_from(table).where(table.c[local_column] == student_id)
                    ).scalar_one()
                    if count:
                        unknown.append({"table": table_name, "column": local_column, "count": int(count)})
            reflected = Table(table_name, db.metadata, autoload_with=db.engine)
            direct_columns = [
                column_name
                for column_name in _STUDENT_PURGE_LINK_COLUMNS
                if column_name in reflected.c
            ]
            if direct_columns:
                unknown.append({
                    "table": table_name,
                    "column": ", ".join(direct_columns),
                    "count": "unknown",
                })
    return unknown


def _student_purge_graph(student_id):
    """Build the complete graph, including legacy tables without FKs."""
    seed = {"students": {student_id}}
    enrollment_table = db.metadata.tables.get("student_enrollments")
    if enrollment_table is not None:
        enrollment_ids = {
            row[0]
            for row in db.session.execute(
                select(enrollment_table.c.id).where(enrollment_table.c.student_id == student_id)
            ).all()
        }
        if enrollment_ids:
            seed["student_enrollments"] = enrollment_ids
    else:
        enrollment_ids = set()

    movement_ids = set()
    for table in db.metadata.tables.values():
        if table.name in _STUDENT_PURGE_EXCLUDED_TABLES or "id" not in table.c:
            continue
        clauses = []
        if "student_id" in table.c:
            clauses.append(table.c.student_id == student_id)
        for column_name in (
            "student_enrollment_id",
            "enrollment_id",
            "source_enrollment_id",
            "destination_enrollment_id",
        ):
            if enrollment_ids and column_name in table.c:
                clauses.append(table.c[column_name].in_(enrollment_ids))
        if not clauses:
            continue
        ids = {
            row[0]
            for row in db.session.execute(select(table.c.id).where(or_(*clauses))).all()
        }
        if ids:
            seed.setdefault(table.name, set()).update(ids)
            if table.name == "student_enrollment_movements":
                movement_ids.update(ids)

    if movement_ids:
        for table in db.metadata.tables.values():
            if table.name in _STUDENT_PURGE_EXCLUDED_TABLES or "id" not in table.c or "movement_id" not in table.c:
                continue
            movement_rows = {
                row[0]
                for row in db.session.execute(
                    select(table.c.id).where(table.c.movement_id.in_(movement_ids))
                ).all()
            }
            if movement_rows:
                seed.setdefault(table.name, set()).update(movement_rows)

    graph = _collect_fk_descendants(seed)
    graph["students"] = {student_id}
    return graph


def _student_enrollment_ids(student_id):
    table = db.metadata.tables.get("student_enrollments")
    if table is None:
        return set()
    return {
        row[0]
        for row in db.session.execute(
            select(table.c.id).where(table.c.student_id == student_id)
        ).all()
    }


def _student_tombstone_filter(table, student_id, enrollment_ids):
    clauses = [table.c.student_id == student_id]
    if enrollment_ids and "student_enrollment_id" in table.c:
        clauses.append(table.c.student_enrollment_id.in_(enrollment_ids))
    return or_(*clauses)


def _student_purge_counts(graph, tombstone_count=0):
    counts = {table_name: len(ids) for table_name, ids in sorted(graph.items()) if ids}
    if tombstone_count:
        counts["behavior_attendance_deletions"] = int(tombstone_count)
    return counts


def _delete_student_photo(path):
    """Remove a student photo from Cloudinary or local uploads when possible."""
    if not path:
        return
    value = str(path)
    if value.startswith(("http://", "https://")) and "res.cloudinary.com" in value:
        cloud_name = current_app.config.get("CLOUDINARY_CLOUD_NAME")
        if not cloud_name:
            raise PurgeValidationError("The student photo is stored in Cloudinary, but Cloudinary is not configured.")
        try:
            import cloudinary
            import cloudinary.uploader

            cloudinary.config(
                cloud_name=cloud_name,
                api_key=current_app.config.get("CLOUDINARY_API_KEY"),
                api_secret=current_app.config.get("CLOUDINARY_API_SECRET"),
                secure=True,
            )
            parts = value.split("/upload/", 1)
            if len(parts) != 2:
                raise PurgeValidationError("The stored student photo URL is not a valid Cloudinary asset URL.")
            public_path = parts[1].split("/", 1)[-1]
            if public_path.startswith("v") and public_path[1:].split("/", 1)[0].isdigit():
                public_path = public_path.split("/", 1)[-1]
            public_id = public_path.rsplit(".", 1)[0]
            result = cloudinary.uploader.destroy(public_id, resource_type="image")
            if result.get("result") not in {"ok", "not found"}:
                raise PurgeValidationError("The student photo could not be removed from Cloudinary.")
            return
        except PurgeValidationError:
            raise
        except Exception as error:
            raise PurgeValidationError(f"The student photo could not be removed from Cloudinary: {error}") from error

    relative = value.removeprefix("/static/").removeprefix("uploads/")
    uploads_root = (Path(current_app.root_path) / "static" / "uploads").resolve()
    candidate = (uploads_root / relative).resolve()
    if candidate == uploads_root or uploads_root not in candidate.parents:
        raise PurgeValidationError("The stored student photo path is outside the allowed uploads directory.")
    if candidate.exists() and candidate.is_file():
        candidate.unlink()


def scan_student_purge(student_id):
    """Return a read-only dependency report for a permanent student purge."""
    student = db.session.get(Student, student_id)
    if not student:
        raise PurgeValidationError("Student was not found.")
    graph = _student_purge_graph(student_id)
    tombstone_table = db.metadata.tables.get("behavior_attendance_deletions")
    enrollment_ids = _student_enrollment_ids(student_id)
    tombstone_count = db.session.execute(
        select(func.count()).select_from(tombstone_table).where(
            _student_tombstone_filter(tombstone_table, student_id, enrollment_ids)
        )
    ).scalar_one() if tombstone_table is not None else 0
    unknown = _student_purge_unknown_dependencies(student_id)
    return {
        "student_id": student.id,
        "student_code": student.student_code,
        "student_name": student.full_name,
        "counts": _student_purge_counts(graph, tombstone_count),
        "total_records": sum(_student_purge_counts(graph, tombstone_count).values()),
        "unknown_dependencies": unknown,
        "photo_path": bool(student.photo_path),
    }


def purge_student(student_id, confirmation):
    """Permanently delete one Student and every related non-audit record."""
    if confirmation != STUDENT_DELETE_CONFIRMATION:
        raise PurgeValidationError(f"Type {STUDENT_DELETE_CONFIRMATION} exactly to continue.")
    student = db.session.get(Student, student_id, with_for_update=True)
    if not student:
        raise PurgeValidationError("Student was not found.")

    report = scan_student_purge(student_id)
    if report["unknown_dependencies"]:
        details = ", ".join(
            f"{item['table']}.{item['column']} ({item['count']})"
            for item in report["unknown_dependencies"]
        )
        raise PurgeValidationError(
            "The purge is blocked because unregistered student dependencies were found: " + details
        )

    photo_path = student.photo_path
    graph = _student_purge_graph(student_id)
    tombstones = db.metadata.tables.get("behavior_attendance_deletions")
    if tombstones is not None:
        db.session.execute(
            tombstones.delete().where(
                _student_tombstone_filter(
                    tombstones,
                    student_id,
                    set(graph.get("student_enrollments", set())),
                )
            )
        )
    _delete_student_photo(photo_path)
    deleted = _delete_fk_graph(graph)
    db.session.flush()

    remaining_student = db.session.execute(
        select(func.count()).select_from(Student.__table__).where(Student.__table__.c.id == student_id)
    ).scalar_one()
    if remaining_student:
        raise PurgeValidationError("Permanent student deletion verification failed.")

    for table_name, column_name in (
        ("behavior_attendance_deletions", "student_id"),
    ):
        table = db.metadata.tables.get(table_name)
        if table is not None:
            remaining = db.session.execute(
                select(func.count()).select_from(table).where(
                    _student_tombstone_filter(
                        table,
                        student_id,
                        set(graph.get("student_enrollments", set())),
                    )
                )
            ).scalar_one()
            if remaining:
                raise PurgeValidationError(f"Permanent deletion verification failed for {table_name}.")
    return report, deleted


# These are the direct AcademicYear foreign keys known to the application.
# The scanner also inspects metadata so a newly added direct FK is reported as
# unsupported instead of being silently skipped by the purge.
KNOWN_DIRECT_YEAR_TABLES = {
    "academic_year_levels",
    "academic_year_subjects",
    "attendance_records",
    "behavior_attendance_records",
    "behavior_configurations",
    "exam_halls",
    "exam_marking_configurations",
    "exam_sessions",
    "exam_types",
    "exams",
    "id_card_issues",
    "promotion_evaluations",
    "promotion_rules",
    "student_enrollments",
    "student_enrollment_movements",
    "students",  # identity is retained; only its legacy snapshot is cleared
    # Legacy table retained by older Behavior subject setup versions. It is
    # reflected and purged even though the current ORM no longer maps it.
    "behavior_subject_scopes",
}

LEGACY_PURGE_TABLES = ("behavior_subject_scopes",)


def _reflect_legacy_purge_tables():
    """Reflect retired tables that still exist in older local databases."""
    inspector = inspect(db.engine)
    existing = set(inspector.get_table_names())
    tables = {}
    for table_name in LEGACY_PURGE_TABLES:
        if table_name not in existing:
            continue
        table = db.metadata.tables.get(table_name)
        if table is None:
            table = Table(table_name, db.metadata, autoload_with=db.engine)
        tables[table_name] = table
    return tables


def _ids(query):
    return {row[0] for row in query.all()}


def _count(model, ids):
    if not ids:
        return 0
    return model.query.filter(model.id.in_(ids)).count()


def _add_entry(entries, category, count, *, retained=False):
    if count:
        entries.append({
            "category": category,
            "count": int(count),
            "retained": bool(retained),
        })


def _year_scope_ids(year_id):
    """Build the actual IDs used by the year-aware dependency graph."""
    legacy_tables = _reflect_legacy_purge_tables()
    year_level_ids = _ids(
        db.session.query(AcademicYearLevel.id).filter(
            AcademicYearLevel.academic_year_id == year_id
        )
    )
    year_class_ids = _ids(
        db.session.query(AcademicYearClass.id).filter(
            AcademicYearClass.academic_year_level_id.in_(year_level_ids)
        )
    )
    year_subject_ids = _ids(
        db.session.query(AcademicYearSubject.id).filter(
            AcademicYearSubject.academic_year_id == year_id
        )
    )
    legacy_class_ids = _ids(
        db.session.query(AcademicYearClass.legacy_class_id).filter(
            AcademicYearClass.id.in_(year_class_ids),
            AcademicYearClass.legacy_class_id.isnot(None),
        )
    )
    exam_type_ids = _ids(
        db.session.query(ExamType.id).filter(ExamType.academic_year_id == year_id)
    )
    exam_marking_configuration_ids = _ids(
        db.session.query(ExamMarkingConfiguration.id).filter(
            ExamMarkingConfiguration.academic_year_id == year_id
        )
    )
    exam_ids = _ids(db.session.query(Exam.id).filter(Exam.academic_year_id == year_id))
    session_ids = _ids(
        db.session.query(ExamSession.id).filter(
            or_(
                ExamSession.academic_year_id == year_id,
                ExamSession.exam_id.in_(exam_ids),
                ExamSession.exam_type_id.in_(exam_type_ids),
            )
        )
    )
    hall_ids = _ids(
        db.session.query(ExamHall.id).filter(
            or_(
                ExamHall.academic_year_id == year_id,
                ExamHall.exam_id.in_(exam_ids),
                ExamHall.exam_type_id.in_(exam_type_ids),
                ExamHall.academic_class_id.in_(legacy_class_ids),
            )
        )
    )
    version_ids = _ids(
        db.session.query(ExamHallVersion.id).filter(
            ExamHallVersion.exam_hall_id.in_(hall_ids)
        )
    )
    session_subject_ids = _ids(
        db.session.query(ExamSessionSubject.id).filter(
            ExamSessionSubject.exam_session_id.in_(session_ids)
        )
    )
    hall_subject_ids = _ids(
        db.session.query(ExamHallSubject.id).filter(
            ExamHallSubject.exam_hall_id.in_(hall_ids)
        )
    )
    hall_enrollment_ids = _ids(
        db.session.query(ExamHallEnrollment.id).filter(
            ExamHallEnrollment.exam_hall_id.in_(hall_ids)
        )
    )
    seat_assignment_ids = _ids(
        db.session.query(SeatAssignment.id).filter(
            or_(
                SeatAssignment.exam_id.in_(exam_ids),
                SeatAssignment.exam_hall_id.in_(hall_ids),
            )
        )
    )
    enrollment_ids = _ids(
        db.session.query(StudentEnrollment.id).filter(
            StudentEnrollment.academic_year_id == year_id
        )
    )
    movement_ids = _ids(
        db.session.query(StudentEnrollmentMovement.id).filter(
            or_(
                StudentEnrollmentMovement.from_academic_year_id == year_id,
                StudentEnrollmentMovement.to_academic_year_id == year_id,
                StudentEnrollmentMovement.enrollment_id.in_(enrollment_ids),
            )
        )
    )
    enrollment_student_ids = _ids(
        db.session.query(StudentEnrollment.student_id).filter(
            StudentEnrollment.id.in_(enrollment_ids)
        )
    )
    legacy_year_student_ids = _ids(
        db.session.query(Student.id).filter(Student.academic_year_id == year_id)
    )
    student_ids = enrollment_student_ids | legacy_year_student_ids

    rule_ids = _ids(
        db.session.query(PromotionRule.id).filter(
            or_(
                PromotionRule.academic_year_id == year_id,
                PromotionRule.academic_year_level_id.in_(year_level_ids),
            )
        )
    )
    evaluation_ids = _ids(
        db.session.query(PromotionEvaluation.id).filter(
            or_(
                PromotionEvaluation.academic_year_id == year_id,
                PromotionEvaluation.student_enrollment_id.in_(enrollment_ids),
                PromotionEvaluation.exam_id.in_(exam_ids),
            )
        )
    )
    outcome_ids = _ids(
        db.session.query(PromotionOutcomeApplication.id).filter(
            or_(
                PromotionOutcomeApplication.promotion_evaluation_id.in_(evaluation_ids),
                PromotionOutcomeApplication.source_enrollment_id.in_(enrollment_ids),
                PromotionOutcomeApplication.destination_enrollment_id.in_(enrollment_ids),
                PromotionOutcomeApplication.movement_id.in_(movement_ids),
            )
        )
    )
    result_ids = _ids(
        db.session.query(Result.id).filter(Result.exam_id.in_(exam_ids))
    )
    verification_ids = _ids(
        db.session.query(ReportVerification.id).filter(
            ReportVerification.exam_id.in_(exam_ids)
        )
    )
    grade_scale_ids = _ids(
        db.session.query(GradeScale.id).filter(GradeScale.exam_id.in_(exam_ids))
    )
    attendance_ids = _ids(
        db.session.query(AttendanceRecord.id).filter(
            or_(
                AttendanceRecord.academic_year_id == year_id,
                AttendanceRecord.exam_id.in_(exam_ids),
                AttendanceRecord.exam_hall_id.in_(hall_ids),
                AttendanceRecord.exam_session_id.in_(session_ids),
                AttendanceRecord.exam_type_id.in_(exam_type_ids),
            )
        )
    )
    id_card_ids = _ids(
        db.session.query(IdCardIssue.id).filter(
            or_(
                IdCardIssue.academic_year_id == year_id,
            )
        )
    )
    feedback_ids = _ids(
        db.session.query(StudentFeedback.id).filter(
            or_(
                StudentFeedback.exam_id.in_(exam_ids),
            )
        )
    )
    complaint_ids = _ids(
        db.session.query(StudentComplaint.id).filter(
            or_(
                StudentComplaint.exam_id.in_(exam_ids),
            )
        )
    )
    incident_ids = _ids(
        db.session.query(IncidentReport.id).filter(
            or_(
                IncidentReport.exam_id.in_(exam_ids),
            )
        )
    )
    behavior_configuration_ids = _ids(
        db.session.query(BehaviorConfiguration.id).filter(
            BehaviorConfiguration.academic_year_id == year_id
        )
    )
    behavior_attendance_record_ids = _ids(
        db.session.query(BehaviorAttendanceRecord.id).filter(
            BehaviorAttendanceRecord.academic_year_id == year_id
        )
    )
    behavior_subject_scope_ids = set()
    legacy_scope_table = legacy_tables.get("behavior_subject_scopes")
    if legacy_scope_table is not None and "academic_year_id" in legacy_scope_table.c:
        behavior_subject_scope_ids = {
            row[0]
            for row in db.session.execute(
                select(legacy_scope_table.c.id).where(
                    legacy_scope_table.c.academic_year_id == year_id
                )
            ).all()
        }

    return {
        "year_level_ids": year_level_ids,
        "year_class_ids": year_class_ids,
        "year_subject_ids": year_subject_ids,
        "exam_type_ids": exam_type_ids,
        "exam_marking_configuration_ids": exam_marking_configuration_ids,
        "exam_ids": exam_ids,
        "session_ids": session_ids,
        "hall_ids": hall_ids,
        "version_ids": version_ids,
        "session_subject_ids": session_subject_ids,
        "hall_subject_ids": hall_subject_ids,
        "hall_enrollment_ids": hall_enrollment_ids,
        "seat_assignment_ids": seat_assignment_ids,
        "enrollment_ids": enrollment_ids,
        "movement_ids": movement_ids,
        "student_ids": student_ids,
        "rule_ids": rule_ids,
        "evaluation_ids": evaluation_ids,
        "outcome_ids": outcome_ids,
        "result_ids": result_ids,
        "verification_ids": verification_ids,
        "grade_scale_ids": grade_scale_ids,
        "attendance_ids": attendance_ids,
        "id_card_ids": id_card_ids,
        "feedback_ids": feedback_ids,
        "complaint_ids": complaint_ids,
        "incident_ids": incident_ids,
        "behavior_configuration_ids": behavior_configuration_ids,
        "behavior_attendance_record_ids": behavior_attendance_record_ids,
        "behavior_subject_scope_ids": behavior_subject_scope_ids,
    }


def _unknown_direct_dependencies(year_id):
    """Report direct year FKs introduced without a purge handler."""
    unknown = []
    for table in db.metadata.tables.values():
        if table.name in KNOWN_DIRECT_YEAR_TABLES:
            continue
        for column in table.columns:
            if not any(fk.target_fullname == "academic_years.id" for fk in column.foreign_keys):
                continue
            count = db.session.execute(
                select(func.count()).select_from(table).where(column == year_id)
            ).scalar_one()
            if count:
                unknown.append({"table": table.name, "column": column.name, "count": int(count)})
    return unknown


_PURGE_SEED_TABLES = {
    "academic_year_levels": "year_level_ids",
    "academic_year_classes": "year_class_ids",
    "academic_year_subjects": "year_subject_ids",
    "exam_types": "exam_type_ids",
    "exam_marking_configurations": "exam_marking_configuration_ids",
    "exams": "exam_ids",
    "exam_sessions": "session_ids",
    "exam_halls": "hall_ids",
    "exam_hall_versions": "version_ids",
    "exam_session_subjects": "session_subject_ids",
    "exam_hall_subjects": "hall_subject_ids",
    "exam_hall_enrollments": "hall_enrollment_ids",
    "seat_assignments": "seat_assignment_ids",
    "student_enrollments": "enrollment_ids",
    "student_enrollment_movements": "movement_ids",
    "promotion_rules": "rule_ids",
    "promotion_evaluations": "evaluation_ids",
    "promotion_outcome_applications": "outcome_ids",
    "results": "result_ids",
    "report_verifications": "verification_ids",
    "grade_scales": "grade_scale_ids",
    "attendance_records": "attendance_ids",
    "behavior_configurations": "behavior_configuration_ids",
    "behavior_attendance_records": "behavior_attendance_record_ids",
    "behavior_subject_scopes": "behavior_subject_scope_ids",
    "id_card_issues": "id_card_ids",
    "student_feedback": "feedback_ids",
    "student_complaints": "complaint_ids",
    "incident_reports": "incident_ids",
}

_PURGE_LABELS = {
    "academic_years": "Academic years",
    "academic_year_levels": "Academic year levels",
    "academic_year_classes": "Academic year classes",
    "academic_year_subjects": "Academic year subjects",
    "behavior_configurations": "Behavior configurations",
    "behavior_sessions": "Behavior sessions",
    "behavior_categories": "Behavior categories",
    "behavior_subcategories": "Behavior sub-categories",
    "behavior_actions": "Behavior actions",
    "behavior_action_choices": "Behavior action choices",
    "behavior_events": "Behavior events",
    "behavior_grade_scales": "Behavior grade scales",
    "behavior_attendance_statuses": "Behavior attendance statuses",
    "behavior_attendance_days": "Behavior attendance days",
    "academic_year_level_attendance_days": "Academic level attendance active days",
    "behavior_attendance_records": "Behavior attendance records",
    "behavior_subject_scopes": "Legacy Behavior subject scopes",
    "exam_marking_configurations": "Exam marking configurations",
    "students": "Student identities deleted",
    "student_enrollments": "Student enrollments",
    "student_enrollment_movements": "Enrollment movements",
    "promotion_rules": "Promotion rules",
    "promotion_evaluations": "Promotion evaluation history",
    "promotion_outcome_applications": "Promotion outcome applications",
}


def _row_ids_for_fk(table, fk_column, parent_ids):
    if not parent_ids or "id" not in table.c:
        return set()
    return {
        row[0]
        for row in db.session.execute(
            select(table.c.id).where(fk_column.in_(parent_ids))
        ).all()
    }


def _collect_fk_descendants(seed):
    """Collect all child rows reachable through actual SQLAlchemy foreign keys."""
    graph = {name: set(ids) for name, ids in seed.items() if ids}
    changed = True
    while changed:
        changed = False
        for child in db.metadata.tables.values():
            if "id" not in child.c:
                continue
            for column in child.columns:
                for fk in column.foreign_keys:
                    parent_name = fk.column.table.name
                    parent_ids = graph.get(parent_name)
                    if not parent_ids:
                        continue
                    # A surviving destination enrollment may point back to a
                    # source enrollment; that history must not be purged.
                    if (
                        child.name == "student_enrollments"
                        and column.name == "previous_enrollment_id"
                    ):
                        continue
                    child_ids = _row_ids_for_fk(child, column, parent_ids)
                    if not child_ids:
                        continue
                    before = len(graph.setdefault(child.name, set()))
                    graph[child.name].update(child_ids)
                    changed |= len(graph[child.name]) != before
    return graph


def _delete_fk_graph(graph):
    """Delete rows in child-before-parent order using the FK graph.

    The graph already contains every reachable row that belongs to the
    archived year.  The previous implementation issued a ``SELECT ...
    LIMIT 1`` for every table/FK pair on every pass to discover leaves.  That
    turned a purge into a large number of round trips and made production
    purges vulnerable to request timeouts.  A table-level child-before-parent
    order is sufficient here: deleting an unrelated row from a child table
    early is safe, and the row graph has already been collected before any
    delete starts.
    """
    remaining = {name: set(ids) for name, ids in graph.items() if ids}
    deleted = 0

    children_by_parent = {}
    for child in db.metadata.tables.values():
        if "id" not in child.c:
            continue
        for column in child.columns:
            for fk in column.foreign_keys:
                if (
                    child.name == "student_enrollments"
                    and column.name == "previous_enrollment_id"
                ):
                    continue
                children_by_parent.setdefault(fk.column.table.name, set()).add(child.name)

    while remaining:
        leaves = [
            table_name
            for table_name in remaining
            if not (children_by_parent.get(table_name, set()) & remaining.keys())
        ]
        if not leaves:
            raise RuntimeError("The purge dependency graph contains an unresolved cycle.")
        for table_name in leaves:
            table = db.metadata.tables[table_name]
            result = db.session.execute(
                table.delete().where(table.c.id.in_(remaining.pop(table_name)))
            )
            deleted += int(result.rowcount or 0)
    return deleted


def _student_identity_split(student_ids, year_id):
    if not student_ids:
        return set(), set()
    retained = _ids(
        db.session.query(StudentEnrollment.student_id).filter(
            StudentEnrollment.student_id.in_(student_ids),
            StudentEnrollment.academic_year_id != year_id,
        )
    )
    removable = set(student_ids) - retained
    return removable, retained


def _build_purge_graph(year_id, scope):
    seed = {"academic_years": {year_id}}
    for table_name, scope_key in _PURGE_SEED_TABLES.items():
        ids = scope.get(scope_key, set())
        if ids:
            seed[table_name] = set(ids)
    removable, retained = _student_identity_split(scope["student_ids"], year_id)
    if removable:
        seed["students"] = removable
    graph = _collect_fk_descendants(seed)
    # The source-year enrollment set is authoritative. Never widen it through
    # the self-referential enrollment history relationship.
    graph["student_enrollments"] = set(scope["enrollment_ids"])
    graph["students"] = removable
    graph["academic_years"] = {year_id}
    return graph, removable, retained


def _purge_schema_issues():
    inspector = inspect(db.engine)
    if not inspector.has_table("students"):
        return []
    column = next(
        (item for item in inspector.get_columns("students") if item["name"] == "academic_year_id"),
        None,
    )
    if column and not column.get("nullable", True):
        return [
            "Phase 4C migration is required: students.academic_year_id must allow NULL before an archived Academic Year can be purged."
        ]
    return []


def _purge_context(year_id):
    """Build the purge inputs once for both preview and execution."""
    scope = _year_scope_ids(year_id)
    graph, removable, retained = _build_purge_graph(year_id, scope)
    return (
        scope,
        graph,
        removable,
        retained,
        _purge_schema_issues(),
        _unknown_direct_dependencies(year_id),
    )


def _purge_report(year, graph, removable, retained, schema_issues, unknown_direct):
    entries = []
    for table_name, ids in graph.items():
        if table_name == "academic_years":
            label = _PURGE_LABELS[table_name]
        else:
            label = _PURGE_LABELS.get(table_name, table_name.replace("_", " ").title())
        _add_entry(entries, label, len(ids), retained=False)
    _add_entry(entries, "Student identities retained (other academic years)", len(retained), retained=True)

    return {
        "entity_type": "academic-years",
        "entity_id": year.id,
        "target_name": year.name,
        "archived": not bool(year.is_current),
        "eligible": not bool(year.is_current) and not schema_issues and not unknown_direct,
        "dependencies": entries,
        "total_affected_records": sum(item["count"] for item in entries if not item["retained"]),
        "retained_student_identities": len(retained),
        "deletable_student_identities": len(removable),
        "unsupported_direct_dependencies": unknown_direct,
        "schema_issues": schema_issues,
    }


def scan_academic_year(year_id):
    """Return a real dependency report for an AcademicYear purge."""
    year = db.session.get(AcademicYear, year_id)
    if not year:
        raise PurgeValidationError("Academic Year was not found.")

    _, graph, removable, retained, schema_issues, unknown_direct = _purge_context(year_id)
    return _purge_report(year, graph, removable, retained, schema_issues, unknown_direct)


def _delete_ids(model, ids):
    if not ids:
        return 0
    return model.query.filter(model.id.in_(ids)).delete(synchronize_session=False)


def purge_academic_year(year_id):
    """Delete one archived academic year and every owned dependent row.

    Student identities shared by another academic year remain intact. The
    caller owns the commit so any exception rolls back the whole operation.
    """
    year = db.session.get(AcademicYear, year_id, with_for_update=True)
    if not year:
        raise PurgeValidationError("Academic Year was not found.")
    if year.is_current:
        raise PurgeValidationError("Only archived Academic Years can use HUGE FORCE DELETE.")

    scope, graph, removable_students, retained_students, schema_issues, unknown_direct = _purge_context(year_id)
    report = _purge_report(
        year,
        graph,
        removable_students,
        retained_students,
        schema_issues,
        unknown_direct,
    )
    if schema_issues:
        raise PurgeValidationError(" ".join(schema_issues))
    if unknown_direct:
        dependencies = ", ".join(
            f"{item['table']}.{item['column']} ({item['count']})"
            for item in unknown_direct
        )
        raise PurgeValidationError(
            "The purge is blocked because these direct Academic Year dependencies "
            f"have no deletion handler yet: {dependencies}."
        )
    for student_id in retained_students:
        student = db.session.get(Student, student_id, with_for_update=True)
        if not student or student.academic_year_id != year_id:
            continue
        surviving = (
            StudentEnrollment.query.filter(
                StudentEnrollment.student_id == student_id,
                StudentEnrollment.academic_year_id != year_id,
            )
            .order_by(StudentEnrollment.enrolled_at.desc(), StudentEnrollment.id.desc())
            .first()
        )
        if surviving:
            student.academic_year_id = surviving.academic_year_id
            student.academic_level_id = surviving.academic_year_level_id
            student.academic_class_id = surviving.academic_year_class_id
            student.class_id = None
            student.level = None
            student.section = None

    # A surviving later enrollment may point back to a purged enrollment.
    StudentEnrollment.query.filter(
        StudentEnrollment.previous_enrollment_id.in_(scope["enrollment_ids"])
    ).update({StudentEnrollment.previous_enrollment_id: None}, synchronize_session=False)

    graph.pop("academic_years", None)
    deleted = _delete_fk_graph(graph)
    db.session.flush()
    deleted += db.session.execute(
        AcademicYear.__table__.delete().where(AcademicYear.id == year_id)
    ).rowcount or 0
    db.session.flush()
    return report, deleted


def scan_dependencies(entity_type, entity_id):
    """Reusable scanner entry point for future archived entity types."""
    if entity_type in {"academic-years", "AcademicYear", "academic_year"}:
        return scan_academic_year(entity_id)
    raise PurgeValidationError(f"No dependency scanner is registered for {entity_type}.")
