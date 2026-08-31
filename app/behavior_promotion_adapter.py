"""Promotion Rules adapter for the isolated Behavior domain.

Behavior never becomes an ordinary ``Result`` row.  This adapter resolves a
canonical year-scoped Exam to one exact BehaviorSession, then delegates score
calculation and grade resolution to the existing Behavior services.
"""

from __future__ import annotations

from . import db
from .behavior_grading import behavior_grade_for_score
from .behavior_service import (
    BehaviorValidationError,
    calculate_session_score,
    validate_behavior_scope,
    validate_enrollment_scope,
    validate_session_scope,
)
from .models import (
    AcademicYearSubject,
    BehaviorConfiguration,
    BehaviorSession,
    Exam,
    StudentEnrollment,
)


VALID_STATUSES = {"VALID", "VALID_BASELINE"}


def _result(status, reason, **values):
    return {
        "status": status,
        "reason": reason,
        **values,
    }


def resolve_behavior_promotion_context(
    academic_year_id,
    academic_year_level_id,
    exam_id,
    academic_year_subject_id,
):
    """Resolve one Behavior subject and its exact canonical Exam session.

    Name matching is intentionally unsupported here.  A legacy
    ``exam_type_id``-only session is reported as unavailable until it has an
    exact canonical ``exam_id`` relationship.
    """
    subject = db.session.get(AcademicYearSubject, academic_year_subject_id)
    try:
        year, level, subject = validate_behavior_scope(
            academic_year_id,
            academic_year_level_id,
            academic_year_subject_id,
        )
    except BehaviorValidationError as exc:
        return _result("INVALID", str(exc), subject=subject)

    exam = db.session.get(Exam, exam_id)
    if not exam or exam.academic_year_id != year.id:
        return _result(
            "INVALID",
            "The Behavior evaluation Exam does not belong to the selected Academic Year.",
            subject=subject,
        )

    configurations = (
        BehaviorConfiguration.query
        .filter_by(
            academic_year_id=year.id,
            academic_year_level_id=level.id,
            academic_year_subject_id=subject.id,
        )
        .all()
    )
    if not configurations:
        return _result(
            "INCOMPLETE",
            "No Behavior configuration exists for the selected Academic Year, Level, and Subject.",
            subject=subject,
            exam=exam,
        )
    if len(configurations) != 1:
        return _result(
            "INVALID",
            "Multiple Behavior configurations exist for the selected scope.",
            subject=subject,
            exam=exam,
        )

    configuration = configurations[0]
    if configuration.status != "active":
        return _result(
            "INVALID",
            "The Behavior configuration is not active.",
            subject=subject,
            exam=exam,
            configuration=configuration,
        )

    sessions = (
        BehaviorSession.query
        .filter_by(
            behavior_configuration_id=configuration.id,
            exam_id=exam.id,
            is_active=True,
        )
        .all()
    )
    if not sessions:
        return _result(
            "INCOMPLETE",
            "No active Behavior Session is linked to the selected Exam ID.",
            subject=subject,
            exam=exam,
            configuration=configuration,
        )
    if len(sessions) != 1:
        return _result(
            "INVALID",
            "Multiple active Behavior Sessions are linked to the selected Exam ID.",
            subject=subject,
            exam=exam,
            configuration=configuration,
        )

    session = sessions[0]
    try:
        validate_session_scope(configuration, exam_id=exam.id)
    except BehaviorValidationError as exc:
        return _result(
            "INVALID",
            str(exc),
            subject=subject,
            exam=exam,
            configuration=configuration,
            session=session,
        )

    return _result(
        "VALID",
        None,
        subject=subject,
        exam=exam,
        configuration=configuration,
        session=session,
        maximum_score=session.maximum_score,
    )


def behavior_promotion_evidence(enrollment, exam, subject):
    """Return normalized Behavior evidence for Promotion Evaluation.

    A configured session with zero events is a valid baseline result.  The
    Behavior scoring service supplies ``maximum / 2`` without this adapter
    inventing or persisting an ordinary Result row.
    """
    if not isinstance(enrollment, StudentEnrollment):
        return _result("INVALID", "A StudentEnrollment is required")
    if not isinstance(exam, Exam):
        return _result("INVALID", "A canonical Exam is required")

    context = resolve_behavior_promotion_context(
        enrollment.academic_year_id,
        enrollment.academic_year_level_id,
        exam.id,
        subject.id,
    )
    if context["status"] not in {"VALID"}:
        return context

    configuration = context["configuration"]
    session = context["session"]
    try:
        validate_enrollment_scope(configuration, enrollment.id)
        score = calculate_session_score(configuration, session, enrollment)
    except BehaviorValidationError as exc:
        return {**context, "status": "INVALID", "reason": str(exc)}

    grade = behavior_grade_for_score(session, score["final_score"])
    grade_status = str(grade.get("grade") or "").upper()
    if grade_status == "NOT CONFIGURED":
        return {**context, "status": "INCOMPLETE", "reason": grade.get("description")}
    if grade_status == "INVALID":
        return {**context, "status": "INVALID", "reason": grade.get("description")}
    status = "VALID_BASELINE" if score["event_count"] == 0 else "VALID"
    return _result(
        status,
        None,
        subject=subject,
        exam=exam,
        configuration=configuration,
        session=session,
        score=score["final_score"],
        maximum_score=score["maximum_score"],
        percentage=score["percentage"],
        base_score=score["base_score"],
        positive_points=score["positive_applied_points"],
        negative_points=score["negative_points"],
        event_count=score["event_count"],
        grade=grade,
        grade_point=grade.get("grade_point", 0.0),
        is_pass=bool(grade.get("is_pass")),
    )
