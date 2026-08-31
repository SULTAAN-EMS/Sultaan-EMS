"""Read-only Behavior projections for the academic reporting surfaces.

Behavior remains separate from ordinary ``Result`` rows.  This module only
resolves the selected student's enrollment and active, year-aware Behavior
configuration, then delegates every session score to ``behavior_service``.
"""

from decimal import Decimal

from .behavior_service import (
    BehaviorValidationError,
    calculate_session_score,
    validate_behavior_configuration,
)
from .behavior_grading import behavior_grade_for_score
from .enrollment_service import resolve_student_academic_context
from .models import AcademicYearSubject, BehaviorConfiguration, BehaviorEvent


def _number(value):
    """Return a report-friendly numeric value without changing precision."""
    return Decimal(str(value or 0)).quantize(Decimal("0.001"))


def _session_exam_name(session):
    if session.exam:
        return session.exam.name
    if session.exam_type:
        return session.exam_type.name
    return session.session_label


def _session_matches_exam(session, exam):
    """Match canonical exams first and legacy sessions by same-year name.

    Reporting can read a published historical exam even when an administrator
    later deactivates that exam.  The session itself must still be active and
    its identity/year must match exactly; operational event validation remains
    owned by ``behavior_service``.
    """
    if not session or not exam:
        return False
    if session.exam_id is not None:
        return bool(session.exam_id == exam.id and session.exam)
    legacy_exam = session.exam_type
    return bool(
        legacy_exam
        and legacy_exam.academic_year_id == exam.academic_year_id
        and (legacy_exam.name or "").strip().casefold() == (exam.name or "").strip().casefold()
    )


def serialize_behavior_reports(reports):
    """Convert the normalized projection to JSON-safe numeric values."""
    serialized = []
    for report in reports or []:
        item = dict(report)
        for key in (
            "annual_score",
            "annual_maximum",
            "percentage",
            "session_score",
            "session_maximum",
            "base_score",
            "positive_points",
            "negative_points",
        ):
            item[key] = (
                float(report[key])
                if report.get(key) is not None
                else None
            )
        item["events"] = [
            dict(event, points=float(event["points"]))
            for event in report.get("events", [])
        ]
        item["session_results"] = []
        for session in report.get("session_results", []):
            session_item = dict(session)
            for key in (
                "maximum_score",
                "final_score",
                "percentage",
                "base_score",
                "positive_points",
                "negative_points",
            ):
                session_item[key] = (
                    float(session[key])
                    if session.get(key) is not None
                    else None
                )
            session_item["events"] = [
                dict(event, points=float(event["points"]))
                for event in session.get("events", [])
            ]
            item["session_results"].append(session_item)
        item["current_sessions"] = [
            next(
                session_item
                for session_item in item["session_results"]
                if session_item["id"] == session["id"]
            )
            for session in report.get("current_sessions", [])
        ]
        serialized.append(item)
    return serialized


def get_behavior_report_data(student, exam):
    """Return normalized Behavior reports for one student and selected exam.

    The function returns one item per active Behavior configuration in the
    student's exact academic-year/level scope.  Each item contains only the
    session mapped to the selected exam.  Annual allocation completeness is an
    administrative validation concern and never prevents a valid individual
    session from appearing in a result report.
    """
    if not student or not exam or not exam.academic_year_id:
        return []

    placement = resolve_student_academic_context(student, exam.academic_year_id)
    if not placement:
        return []
    year_level_id = placement.get("academic_year_level_id")
    enrollment = placement.get("enrollment")
    if not year_level_id or not enrollment:
        return []
    if enrollment.status not in {"active", "completed"}:
        return []

    configurations = (
        BehaviorConfiguration.query
        .join(
            AcademicYearSubject,
            AcademicYearSubject.id == BehaviorConfiguration.academic_year_subject_id,
        )
        .filter(
            BehaviorConfiguration.academic_year_id == exam.academic_year_id,
            BehaviorConfiguration.academic_year_level_id == year_level_id,
            AcademicYearSubject.subject_kind == "behavior",
            AcademicYearSubject.is_active.is_(True),
        )
        .order_by(BehaviorConfiguration.id.asc())
        .all()
    )

    reports = []
    for configuration in configurations:
        try:
            validate_behavior_configuration(configuration)
            sessions = [item for item in configuration.sessions if item.is_active]
        except BehaviorValidationError:
            # A stale or inconsistent configuration must not leak into a
            # report.  The admin configuration page remains responsible for
            # showing and correcting that invalid state.
            continue

        selected_session = None
        for session in sessions:
            linked_exam = session.exam if session.exam_id is not None else session.exam_type
            if not linked_exam:
                continue
            if linked_exam.academic_year_id != configuration.academic_year_id:
                continue
            configured_level = configuration.academic_year_level
            linked_level_id = getattr(linked_exam, "academic_level_id", None)
            if (
                linked_level_id is not None
                and configured_level
                and configured_level.legacy_level_id is not None
                and linked_level_id != configured_level.legacy_level_id
            ):
                continue
            if _session_matches_exam(session, exam):
                selected_session = session
                break

        base_report = {
            "configuration_id": configuration.id,
            "subject_id": configuration.academic_year_subject_id,
            "subject_name": configuration.behavior_subject.name,
            "academic_year_id": exam.academic_year_id,
            "academic_year_level_id": year_level_id,
            "available": bool(selected_session),
            "message": None,
            "session_id": selected_session.id if selected_session else None,
            "session_label": selected_session.session_label if selected_session else None,
            "exam_name": _session_exam_name(selected_session) if selected_session else exam.name,
            "session_results": [],
            "current_sessions": [],
            "events": [],
            "annual_score": None,
            "annual_maximum": None,
            "percentage": None,
            "session_score": None,
            "session_maximum": None,
            "base_score": None,
            "positive_points": None,
            "negative_points": None,
            "grade": None,
        }
        if not selected_session:
            base_report["message"] = (
                "Behavior assessment is not yet available for this examination."
            )
            reports.append(base_report)
            continue

        try:
            score = calculate_session_score(
                configuration,
                selected_session,
                enrollment,
            )
        except BehaviorValidationError:
            continue
        events = (
            BehaviorEvent.query
            .filter_by(
                behavior_configuration_id=configuration.id,
                behavior_session_id=selected_session.id,
                student_enrollment_id=enrollment.id,
                status="active",
            )
            .order_by(BehaviorEvent.occurred_at.asc(), BehaviorEvent.id.asc())
            .all()
        )
        event_rows = [
            {
                "id": event.id,
                "category_name": event.category_name_snapshot,
                "action_name": event.action_name_snapshot,
                "polarity": event.polarity,
                "points": _number(event.points_applied),
                "notes": event.notes or "",
                "occurred_at": event.occurred_at.isoformat() if event.occurred_at else None,
            }
            for event in events
        ]
        session_result = {
            "id": selected_session.id,
            "session_label": selected_session.session_label,
            "exam_name": _session_exam_name(selected_session),
            "maximum_score": _number(score["maximum_score"]),
            "final_score": _number(score["final_score"]),
            "percentage": _number(score["percentage"]),
            "base_score": _number(score["base_score"]),
            "positive_points": _number(score["positive_applied_points"]),
            "negative_points": _number(score["negative_points"]),
            "event_count": score["event_count"],
            "is_current": True,
            "events": event_rows,
        }
        grade = behavior_grade_for_score(
            selected_session,
            session_result["final_score"],
        )
        session_result["grade"] = grade
        session_result["grade_point"] = grade.get("grade_point", 0.0)
        base_report.update(
            {
                # These fields remain as compatibility aliases for the class
                # report templates; they now represent the selected session.
                "annual_score": session_result["final_score"],
                "annual_maximum": session_result["maximum_score"],
                "percentage": session_result["percentage"],
                "session_score": session_result["final_score"],
                "session_maximum": session_result["maximum_score"],
                "base_score": session_result["base_score"],
                "positive_points": session_result["positive_points"],
                "negative_points": session_result["negative_points"],
                "session_results": [session_result],
                "current_sessions": [session_result],
                "events": event_rows,
                "grade": grade,
                "grade_point": grade.get("grade_point", 0.0),
            }
        )
        reports.append(base_report)
    return reports
