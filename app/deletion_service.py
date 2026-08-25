"""Dependency-aware destructive deletion services.

The normal Configuration Center lifecycle is intentionally kept in
``routes_admin``.  This module owns the separate, final-stage purge so its
dependency graph and transaction boundary cannot be confused with a normal
delete.
"""

from sqlalchemy import inspect, or_, select, func

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
)


class PurgeValidationError(ValueError):
    """A purge cannot safely proceed with the current data or authorization."""


# These are the direct AcademicYear foreign keys known to the application.
# The scanner also inspects metadata so a newly added direct FK is reported as
# unsupported instead of being silently skipped by the purge.
KNOWN_DIRECT_YEAR_TABLES = {
    "academic_year_levels",
    "academic_year_subjects",
    "attendance_records",
    "exam_halls",
    "exam_sessions",
    "exam_types",
    "exams",
    "id_card_issues",
    "promotion_evaluations",
    "promotion_rules",
    "student_enrollments",
    "student_enrollment_movements",
    "students",  # identity is retained; only its legacy snapshot is cleared
}


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

    return {
        "year_level_ids": year_level_ids,
        "year_class_ids": year_class_ids,
        "year_subject_ids": year_subject_ids,
        "exam_type_ids": exam_type_ids,
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
    """Delete leaf rows first using the database's actual FK graph."""
    remaining = {name: set(ids) for name, ids in graph.items() if ids}
    deleted = 0
    while remaining:
        leaves = []
        for parent_name in remaining:
            has_child = False
            for child in db.metadata.tables.values():
                if child.name not in remaining:
                    continue
                for column in child.columns:
                    for fk in column.foreign_keys:
                        if fk.column.table.name != parent_name:
                            continue
                        if (
                            child.name == "student_enrollments"
                            and column.name == "previous_enrollment_id"
                        ):
                            continue
                        if db.session.execute(
                            select(child.c.id)
                            .where(column.in_(remaining[parent_name]))
                            .limit(1)
                        ).first():
                            has_child = True
                            break
                    if has_child:
                        break
                if has_child:
                    break
            if not has_child:
                leaves.append(parent_name)
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
    removable, retained = set(), set()
    for student_id in student_ids:
        has_other_year = db.session.execute(
            select(StudentEnrollment.id)
            .where(
                StudentEnrollment.student_id == student_id,
                StudentEnrollment.academic_year_id != year_id,
            )
            .limit(1)
        ).first()
        (retained if has_other_year else removable).add(student_id)
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


def scan_academic_year(year_id):
    """Return a real dependency report for an AcademicYear purge."""
    year = db.session.get(AcademicYear, year_id)
    if not year:
        raise PurgeValidationError("Academic Year was not found.")

    scope = _year_scope_ids(year_id)
    graph, removable, retained = _build_purge_graph(year_id, scope)
    entries = []
    for table_name, ids in graph.items():
        if table_name == "academic_years":
            label = _PURGE_LABELS[table_name]
        else:
            label = _PURGE_LABELS.get(table_name, table_name.replace("_", " ").title())
        _add_entry(entries, label, len(ids), retained=False)
    _add_entry(entries, "Student identities retained (other academic years)", len(retained), retained=True)
    schema_issues = _purge_schema_issues()
    unknown_direct = _unknown_direct_dependencies(year_id)

    return {
        "entity_type": "academic-years",
        "entity_id": year.id,
        "target_name": year.name,
        "archived": not bool(year.is_current),
        "eligible": not bool(year.is_current) and not schema_issues,
        "dependencies": entries,
        "total_affected_records": sum(item["count"] for item in entries if not item["retained"]),
        "retained_student_identities": len(retained),
        "deletable_student_identities": len(removable),
        "unsupported_direct_dependencies": unknown_direct,
        "schema_issues": schema_issues,
    }


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

    report = scan_academic_year(year_id)
    if report.get("schema_issues"):
        raise PurgeValidationError(" ".join(report["schema_issues"]))
    scope = _year_scope_ids(year_id)

    graph, removable_students, retained_students = _build_purge_graph(year_id, scope)
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
