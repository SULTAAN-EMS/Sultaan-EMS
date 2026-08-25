"""Dependency-aware destructive deletion services.

The normal Configuration Center lifecycle is intentionally kept in
``routes_admin``.  This module owns the separate, final-stage purge so its
dependency graph and transaction boundary cannot be confused with a normal
delete.
"""

from sqlalchemy import or_, select, func

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


def scan_academic_year(year_id):
    """Return a real dependency report for an AcademicYear purge."""
    year = db.session.get(AcademicYear, year_id)
    if not year:
        raise PurgeValidationError("Academic Year was not found.")

    scope = _year_scope_ids(year_id)
    entries = []
    _add_entry(entries, "Academic year levels", len(scope["year_level_ids"]))
    _add_entry(entries, "Academic year classes", len(scope["year_class_ids"]))
    _add_entry(entries, "Academic year subjects", len(scope["year_subject_ids"]))
    _add_entry(entries, "Exam types", len(scope["exam_type_ids"]))
    _add_entry(entries, "Exams", len(scope["exam_ids"]))
    _add_entry(entries, "Exam sessions", len(scope["session_ids"]))
    _add_entry(entries, "Exam halls", len(scope["hall_ids"]))
    _add_entry(entries, "Exam hall versions", len(scope["version_ids"]))
    _add_entry(entries, "Seat assignments", len(scope["seat_assignment_ids"]))
    _add_entry(entries, "Exam session subject assignments", len(scope["session_subject_ids"]))
    _add_entry(entries, "Exam hall subjects", len(scope["hall_subject_ids"]))
    _add_entry(entries, "Exam hall enrollments", len(scope["hall_enrollment_ids"]))
    _add_entry(entries, "Seat mixer assignments", _count(SeatMixerAssignment, scope["version_ids"]))
    _add_entry(entries, "Seat mixer snapshots", _count(SeatMixerSaveSnapshot, scope["version_ids"]))
    _add_entry(entries, "Promotion rules", len(scope["rule_ids"]))
    _add_entry(
        entries,
        "Critical subject configurations",
        PromotionRuleCriticalSubject.query.filter(
            or_(
                PromotionRuleCriticalSubject.promotion_rule_id.in_(scope["rule_ids"]),
                PromotionRuleCriticalSubject.academic_year_subject_id.in_(scope["year_subject_ids"]),
            )
        ).count(),
    )
    _add_entry(entries, "Student enrollments", len(scope["enrollment_ids"]))
    _add_entry(entries, "Enrollment movements", len(scope["movement_ids"]))
    _add_entry(entries, "Promotion evaluation history", len(scope["evaluation_ids"]))
    _add_entry(entries, "Promotion outcome applications", len(scope["outcome_ids"]))
    _add_entry(entries, "Results", len(scope["result_ids"]))
    _add_entry(entries, "Report verifications", len(scope["verification_ids"]))
    _add_entry(entries, "Grade scales", len(scope["grade_scale_ids"]))
    _add_entry(entries, "Attendance records", len(scope["attendance_ids"]))
    _add_entry(entries, "ID card issues", len(scope["id_card_ids"]))
    _add_entry(entries, "Student feedback", len(scope["feedback_ids"]))
    _add_entry(
        entries,
        "Feedback replies",
        StudentFeedbackReply.query.filter(StudentFeedbackReply.feedback_id.in_(scope["feedback_ids"])).count(),
    )
    _add_entry(entries, "Student complaints", len(scope["complaint_ids"]))
    _add_entry(
        entries,
        "Complaint replies",
        StudentComplaintReply.query.filter(StudentComplaintReply.complaint_id.in_(scope["complaint_ids"])).count(),
    )
    _add_entry(entries, "Incident reports", len(scope["incident_ids"]))
    _add_entry(
        entries,
        "Incident report categories",
        IncidentReportCategory.query.filter(IncidentReportCategory.report_id.in_(scope["incident_ids"])).count(),
    )
    _add_entry(
        entries,
        "Incident attachments",
        IncidentAttachment.query.filter(IncidentAttachment.report_id.in_(scope["incident_ids"])).count(),
    )
    _add_entry(
        entries,
        "Student master identities retained",
        len(scope["student_ids"]),
        retained=True,
    )

    unknown_direct = _unknown_direct_dependencies(year_id)
    for item in unknown_direct:
        _add_entry(
            entries,
            f"Unsupported direct dependency: {item['table']}.{item['column']}",
            item["count"],
        )

    return {
        "entity_type": "academic-years",
        "entity_id": year.id,
        "target_name": year.name,
        "archived": not bool(year.is_current),
        "eligible": not bool(year.is_current) and not unknown_direct,
        "dependencies": entries,
        "total_affected_records": sum(item["count"] for item in entries if not item["retained"]),
        "retained_student_identities": len(scope["student_ids"]),
        "unsupported_direct_dependencies": unknown_direct,
    }


def _delete_ids(model, ids):
    if not ids:
        return 0
    return model.query.filter(model.id.in_(ids)).delete(synchronize_session=False)


def purge_academic_year(year_id):
    """Delete one archived academic year and its graph, without deleting Students.

    The caller owns the commit.  Any exception must be allowed to propagate so
    the caller can roll the entire SQLAlchemy transaction back.
    """
    year = db.session.get(AcademicYear, year_id, with_for_update=True)
    if not year:
        raise PurgeValidationError("Academic Year was not found.")
    if year.is_current:
        raise PurgeValidationError("Only archived Academic Years can use HUGE FORCE DELETE.")

    report = scan_academic_year(year_id)
    if report["unsupported_direct_dependencies"]:
        raise PurgeValidationError(
            "The purge is blocked because the dependency scanner found an unsupported direct dependency."
        )
    scope = _year_scope_ids(year_id)

    # Preserve the permanent Student identity.  The legacy placement snapshot
    # is moved to the latest surviving enrollment, or cleared when no placement
    # remains.  This is why students.academic_year_id is nullable in the model.
    for student in Student.query.filter(Student.id.in_(scope["student_ids"])).with_for_update().all():
        if student.academic_year_id != year_id:
            continue
        surviving = (
            StudentEnrollment.query.filter(
                StudentEnrollment.student_id == student.id,
                StudentEnrollment.academic_year_id != year_id,
            )
            .order_by(StudentEnrollment.enrolled_at.desc(), StudentEnrollment.id.desc())
            .first()
        )
        student.academic_year_id = surviving.academic_year_id if surviving else None
        if surviving is None:
            student.class_id = None
            student.academic_level_id = None
            student.academic_class_id = None
            student.academic_section_id = None
            student.level = None
            student.section = None

    # A surviving later enrollment may point back to a purged enrollment.
    StudentEnrollment.query.filter(
        StudentEnrollment.previous_enrollment_id.in_(scope["enrollment_ids"])
    ).update({StudentEnrollment.previous_enrollment_id: None}, synchronize_session=False)

    deleted = 0
    deleted += _delete_ids(StudentFeedbackReply, {
        row[0] for row in db.session.query(StudentFeedbackReply.id).filter(
            StudentFeedbackReply.feedback_id.in_(scope["feedback_ids"])
        ).all()
    })
    deleted += _delete_ids(StudentComplaintReply, {
        row[0] for row in db.session.query(StudentComplaintReply.id).filter(
            StudentComplaintReply.complaint_id.in_(scope["complaint_ids"])
        ).all()
    })
    deleted += _delete_ids(IncidentAttachment, {
        row[0] for row in db.session.query(IncidentAttachment.id).filter(
            IncidentAttachment.report_id.in_(scope["incident_ids"])
        ).all()
    })
    deleted += _delete_ids(IncidentReportCategory, {
        row[0] for row in db.session.query(IncidentReportCategory.id).filter(
            IncidentReportCategory.report_id.in_(scope["incident_ids"])
        ).all()
    })
    deleted += _delete_ids(SeatMixerSaveSnapshot, scope["version_ids"])
    deleted += _delete_ids(SeatMixerAssignment, scope["version_ids"])
    deleted += _delete_ids(SeatAssignment, scope["seat_assignment_ids"])
    deleted += _delete_ids(ExamHallEnrollment, scope["hall_enrollment_ids"])
    deleted += _delete_ids(ExamHallSubject, scope["hall_subject_ids"])
    deleted += _delete_ids(PromotionOutcomeApplication, scope["outcome_ids"])
    deleted += _delete_ids(PromotionEvaluation, scope["evaluation_ids"])
    deleted += _delete_ids(StudentEnrollmentMovement, scope["movement_ids"])
    deleted += _delete_ids(Result, scope["result_ids"])
    deleted += _delete_ids(ReportVerification, scope["verification_ids"])
    deleted += _delete_ids(GradeScale, scope["grade_scale_ids"])
    deleted += _delete_ids(AttendanceRecord, scope["attendance_ids"])
    deleted += _delete_ids(IdCardIssue, scope["id_card_ids"])
    deleted += _delete_ids(StudentFeedback, scope["feedback_ids"])
    deleted += _delete_ids(StudentComplaint, scope["complaint_ids"])
    deleted += _delete_ids(IncidentReport, scope["incident_ids"])
    deleted += _delete_ids(ExamSessionSubject, scope["session_subject_ids"])
    deleted += _delete_ids(ExamHallVersion, scope["version_ids"])
    deleted += _delete_ids(ExamHall, scope["hall_ids"])
    deleted += _delete_ids(ExamSession, scope["session_ids"])
    deleted += _delete_ids(PromotionRuleCriticalSubject, {
        row[0] for row in db.session.query(PromotionRuleCriticalSubject.id).filter(
            or_(
                PromotionRuleCriticalSubject.promotion_rule_id.in_(scope["rule_ids"]),
                PromotionRuleCriticalSubject.academic_year_subject_id.in_(scope["year_subject_ids"]),
            )
        ).all()
    })
    deleted += _delete_ids(PromotionRule, scope["rule_ids"])
    deleted += _delete_ids(ExamType, scope["exam_type_ids"])
    deleted += _delete_ids(Exam, scope["exam_ids"])
    deleted += _delete_ids(StudentEnrollment, scope["enrollment_ids"])
    deleted += _delete_ids(AcademicYearSubject, scope["year_subject_ids"])
    deleted += _delete_ids(AcademicYearClass, scope["year_class_ids"])
    deleted += _delete_ids(AcademicYearLevel, scope["year_level_ids"])

    db.session.delete(year)
    db.session.flush()
    deleted += 1
    return report, deleted


def scan_dependencies(entity_type, entity_id):
    """Reusable scanner entry point for future archived entity types."""
    if entity_type in {"academic-years", "AcademicYear", "academic_year"}:
        return scan_academic_year(entity_id)
    raise PurgeValidationError(f"No dependency scanner is registered for {entity_type}.")
