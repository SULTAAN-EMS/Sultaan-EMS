"""Read-only Behavior projections for the academic reporting surfaces.

Behavior remains separate from ordinary ``Result`` rows.  This module only
resolves the selected student's enrollment and active, year-aware Behavior
configuration, then delegates every session score to ``behavior_service``.
"""

import json
from collections import defaultdict
from decimal import Decimal

from .behavior_service import (
    BehaviorValidationError,
    attendance_points_projection,
    calculate_annual_behavior_score,
    calculate_session_score,
    validate_behavior_configuration,
)
from .behavior_grading import behavior_grade_for_score
from .enrollment_service import resolve_student_academic_context
from .promotion_service import PromotionValidationError
from .models import (
    AcademicYearSubject,
    BehaviorAttendanceRecord,
    BehaviorConfiguration,
    BehaviorEvent,
)


def _number(value):
    """Return a report-friendly numeric value without changing precision."""
    if value is None:
        return None
    return Decimal(str(value)).quantize(Decimal("0.001"))


def _raw_score_state(score, maximum, grade=None, *, legacy=False):
    """Return the pass/fail presentation state from Behavior grading."""
    if score is None or maximum is None:
        return None, "unavailable"
    score = _number(score)
    maximum = _number(maximum)
    if grade and grade.get("grade") not in {"INVALID", "NOT CONFIGURED"}:
        passed = bool(grade.get("is_pass"))
    elif legacy:
        # Historical sessions predate the Behavior-owned grade scale. Keep
        # their established half-of-session threshold without applying it to
        # newly normalized sessions that require a configured grade scale.
        passed = score >= (maximum / Decimal("2"))
    else:
        return None, "unavailable"
    return passed, "pass" if passed else "fail"


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


def _event_response_payload(event):
    """Expose the immutable response snapshot without trusting live config."""
    payload = {}
    try:
        payload = json.loads(event.response_snapshot or "{}")
    except (TypeError, ValueError):
        payload = {}
    return {
        "response_type": event.response_type_snapshot or payload.get("response_type") or "short_answer",
        "response": event.response_display_snapshot or payload.get("text") or "",
        "selected_options": payload.get("selected_options") or [],
        "rating": event.response_rating if event.response_rating is not None else payload.get("rating"),
        "rating_scale": event.response_rating_scale if event.response_rating_scale is not None else payload.get("rating_scale"),
        "action_response": event.response_text or payload.get("text") or "",
        "official_note": event.notes or "",
        "response_points": _number(event.response_points if event.response_points is not None else event.points_applied),
        "subcategory_name": event.subcategory_name_snapshot or "",
    }


_ATTENDANCE_STATUS_LABELS = {
    "present": "Joogid",
    "late": "Daahid",
    "absent": "Maqnaansho",
    "excused": "Cudurdaar",
    "official_leave": "Fasaxid Rasmi ah",
}


def _attendance_record_payload(record):
    """Expose the saved attendance evidence without recalculating a score."""
    status_key = (record.status_key_snapshot or "").strip().lower().replace("-", "_").replace(" ", "_")
    return {
        "id": record.id,
        "attendance_date": record.attendance_date.isoformat() if record.attendance_date else None,
        "status_key": status_key,
        "status_label": _ATTENDANCE_STATUS_LABELS.get(status_key, record.status_label_snapshot or status_key),
        "arrival_time": record.arrival_time.isoformat() if record.arrival_time else None,
        "attendance_time": record.attendance_time.isoformat() if record.attendance_time else None,
        "late_by_minutes": record.late_by_minutes,
        "polarity": record.polarity,
        "points": _number(record.points_applied),
        "note": record.note or "",
    }


def serialize_behavior_reports(reports):
    """Convert the normalized projection to JSON-safe numeric values."""
    def serialize_value(value):
        if isinstance(value, dict):
            return {key: serialize_value(item) for key, item in value.items()}
        if isinstance(value, list):
            return [serialize_value(item) for item in value]
        if isinstance(value, Decimal):
            return float(value)
        return value

    def serialize_attendance(value):
        return serialize_value(dict(value or {}))

    serialized = []
    for report in reports or []:
        item = dict(report)
        for key in (
            "annual_score",
            "annual_maximum",
            "annual_total_score",
            "annual_total_maximum",
            "annual_percentage",
            "percentage",
            "session_score",
            "session_maximum",
            "base_score",
            "positive_points",
            "negative_points",
            "behavior_score",
            "behavior_positive_points",
            "behavior_negative_points",
            "behavior_positive_capacity",
            "behavior_negative_capacity",
            "attendance_score",
            "attendance_positive_points",
            "attendance_negative_points",
            "behavior_allocation",
            "attendance_allocation",
        ):
            item[key] = (
                float(report[key])
                if report.get(key) is not None
                else None
            )
        item["annual_session_count"] = int(report.get("annual_session_count") or 0)
        item["events"] = []
        for event in report.get("events", []):
            event_item = dict(event, points=float(event["points"]))
            if event_item.get("response_points") is not None:
                event_item["response_points"] = float(event_item["response_points"])
            item["events"].append(event_item)
        item["session_results"] = []
        for session in report.get("session_results", []):
            session_item = dict(session)
            session_item["attendance"] = serialize_attendance(session.get("attendance"))
            session_item["attendance_records"] = serialize_value(session.get("attendance_records"))
            session_item["ledger"] = serialize_value(session.get("ledger"))
            for key in (
                "maximum_score",
                "final_score",
                "percentage",
                "base_score",
                "positive_points",
                "negative_points",
                "behavior_score",
                "behavior_positive_points",
                "behavior_negative_points",
                "behavior_positive_capacity",
                "behavior_negative_capacity",
                "attendance_score",
                "attendance_positive_points",
                "attendance_negative_points",
                "behavior_allocation",
                "attendance_allocation",
            ):
                session_item[key] = (
                    float(session[key])
                    if session.get(key) is not None
                    else None
                )
            session_item["events"] = []
            for event in session.get("events", []):
                event_item = dict(event, points=float(event["points"]))
                if event_item.get("response_points") is not None:
                    event_item["response_points"] = float(event_item["response_points"])
                session_item["events"].append(event_item)
            item["session_results"].append(session_item)
        item["ledger"] = serialize_value(report.get("ledger"))
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

    # Critical status is resolved through the same exact Year + Level + Exam
    # rule used by ordinary result rows. Behavior uses a namespaced key because
    # its AcademicYearSubject ID is not a legacy Subject ID.
    from .services import critical_subject_badges

    try:
        critical_badges = critical_subject_badges(exam, year_level_id)
    except PromotionValidationError:
        # Legacy ExamType-backed Behavior sessions do not have a Results Exam
        # scope. Their report remains valid; only the optional critical badge
        # lookup is unavailable for that compatibility path.
        critical_badges = {}

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

        scoped_sessions = []
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
            scoped_sessions.append(session)

        # Canonical Exam IDs always win. A legacy same-name lookup is only a
        # compatibility path for sessions that have no canonical exam_id; it
        # must never override or compete with an exact relationship.
        exact_sessions = [
            session
            for session in scoped_sessions
            if session.exam_id == exam.id and session.exam is not None
        ]
        legacy_sessions = [
            session
            for session in scoped_sessions
            if session.exam_id is None and _session_matches_exam(session, exam)
        ]
        selected_session = None
        selection_error = None
        if len(exact_sessions) > 1:
            selection_error = (
                "Behavior assessment is invalid because multiple sessions are linked to the selected examination."
            )
        elif exact_sessions:
            selected_session = exact_sessions[0]
        elif len(legacy_sessions) > 1:
            selection_error = (
                "Behavior assessment is invalid because multiple legacy sessions match the selected examination."
            )
        elif legacy_sessions:
            selected_session = legacy_sessions[0]

        base_report = {
            "configuration_id": configuration.id,
            "subject_id": configuration.academic_year_subject_id,
            "subject_name": configuration.behavior_subject.name,
            "academic_year_id": exam.academic_year_id,
            "academic_year_level_id": year_level_id,
            "available": bool(selected_session),
            "message": selection_error,
            "availability_status": "INVALID" if selection_error else "AVAILABLE" if selected_session else "UNAVAILABLE",
            "session_id": selected_session.id if selected_session else None,
            "session_label": selected_session.session_label if selected_session else None,
            "exam_name": _session_exam_name(selected_session) if selected_session else exam.name,
            "session_results": [],
            "current_sessions": [],
            "events": [],
            "annual_score": None,
            "annual_maximum": None,
            "annual_total_score": None,
            "annual_total_maximum": None,
            "annual_percentage": None,
            "annual_status": None,
            "annual_reason": None,
            "annual_session_count": 0,
            "percentage": None,
            "session_score": None,
            "session_maximum": None,
            "base_score": None,
            "positive_points": None,
            "negative_points": None,
            "behavior_score": None,
            "behavior_positive_points": None,
            "behavior_negative_points": None,
            "attendance_score": None,
            "attendance_positive_points": None,
            "attendance_negative_points": None,
            "grade": None,
            "grade_point": 0.0,
            "is_pass": None,
            "score_tone": "unavailable",
            "scoring_status": None,
            "scoring_reason": None,
            "critical_badge": critical_badges.get(
                f"behavior:{configuration.academic_year_subject_id}"
            ),
        }
        if not selected_session:
            if not selection_error:
                base_report["message"] = (
                    "Behavior assessment is not yet available for this examination."
                )
            reports.append(base_report)
            continue

        # Read the configuration's student ledger in two bounded queries and
        # reuse those rows for both the selected session and annual summary.
        # This removes the per-session duplicate reads used by report pages.
        events_by_session = defaultdict(list)
        for event in (
            BehaviorEvent.query
            .filter_by(
                behavior_configuration_id=configuration.id,
                student_enrollment_id=enrollment.id,
                status="active",
            )
            .order_by(BehaviorEvent.occurred_at.asc(), BehaviorEvent.id.asc())
            .all()
        ):
            events_by_session[event.behavior_session_id].append(event)
        attendance_by_session = defaultdict(list)
        for record in (
            BehaviorAttendanceRecord.query
            .filter_by(
                behavior_configuration_id=configuration.id,
                student_enrollment_id=enrollment.id,
            )
            .order_by(BehaviorAttendanceRecord.attendance_date.asc(), BehaviorAttendanceRecord.id.asc())
            .all()
        ):
            attendance_by_session[record.behavior_session_id].append(record)

        try:
            score = calculate_session_score(
                configuration,
                selected_session,
                enrollment,
                behavior_events=events_by_session.get(selected_session.id, []),
                attendance_records=attendance_by_session.get(selected_session.id, []),
            )
            annual = calculate_annual_behavior_score(
                configuration,
                enrollment,
                behavior_events_by_session=events_by_session,
                attendance_records_by_session=attendance_by_session,
            )
        except BehaviorValidationError:
            continue
        events = events_by_session.get(selected_session.id, [])
        attendance_rows = attendance_by_session.get(selected_session.id, [])
        attendance_points = attendance_points_projection(attendance_rows)
        attendance = {
            "total": len(attendance_rows),
            "present": sum(row.status_key_snapshot == "present" for row in attendance_rows),
            "late": sum(row.status_key_snapshot == "late" for row in attendance_rows),
            "absent": sum(row.status_key_snapshot == "absent" for row in attendance_rows),
            "excused": sum(row.status_key_snapshot == "excused" for row in attendance_rows),
            "official_leave": sum(row.status_key_snapshot == "official_leave" for row in attendance_rows),
            "points": _number(attendance_points["signed_total"]),
            "positive_points": _number(attendance_points["positive_points"]),
            "negative_points": _number(attendance_points["negative_points"]),
            # Attendance points are read from the same immutable row snapshots
            # used by the canonical session scorer.
            "earned_score": _number((score.get("attendance") or {}).get("attendance_score")),
            "allocation": _number((score.get("attendance") or {}).get("allocation")),
            "positive_applied": _number((score.get("attendance") or {}).get("positive_applied")),
            "negative_applied": _number((score.get("attendance") or {}).get("negative_applied")),
            "scoring_status": score.get("scoring_status", score.get("attendance_status")),
            "scoring_reason": score.get("attendance_reason") or score.get("behavior_reason"),
        }
        attendance_records = [_attendance_record_payload(row) for row in attendance_rows]
        event_rows = [
            {
                "id": event.id,
                "category_name": event.category_name_snapshot,
                "action_name": event.action_name_snapshot,
                "polarity": event.polarity,
                "points": _number(event.points_applied),
                "notes": event.notes or "",
                "occurred_at": event.occurred_at.isoformat() if event.occurred_at else None,
                **_event_response_payload(event),
            }
            for event in events
        ]
        session_result = {
            "id": selected_session.id,
            "session_label": selected_session.session_label,
            "exam_name": _session_exam_name(selected_session),
            "maximum_score": _number(score["maximum_score"]),
            "behavior_allocation": _number(score.get("behavior_allocation")),
            "attendance_allocation": _number(score.get("attendance_allocation")),
            "final_score": _number(score["final_score"]),
            "percentage": _number(score["percentage"]),
            "base_score": _number(score["base_score"]),
            "positive_points": _number(score["positive_applied_points"]),
            "negative_points": _number(score["negative_points"]),
            "behavior_score": _number(score.get("behavior_score")),
            "behavior_positive_points": _number(score.get("behavior_positive_points")),
            "behavior_negative_points": _number(score.get("behavior_negative_points")),
            "attendance_score": _number(score.get("attendance_score")),
            "attendance_positive_points": _number(score.get("attendance_positive_points")),
            "attendance_negative_points": _number(score.get("attendance_negative_points")),
            "event_count": score["event_count"],
            "behavior_status": score.get("behavior_status"),
            "behavior_reason": score.get("behavior_reason"),
            "attendance_status": score.get("attendance_status"),
            "attendance_reason": score.get("attendance_reason"),
            "scoring_status": score.get("scoring_status"),
            "scoring_reason": score.get("attendance_reason") or score.get("behavior_reason"),
            "is_current": True,
            "events": event_rows,
            "attendance": attendance,
            "attendance_records": attendance_records,
            "ledger": score.get("ledger"),
        }
        grade = (
            behavior_grade_for_score(selected_session, session_result["final_score"])
            if session_result["final_score"] is not None else None
        )
        session_result["grade"] = grade
        session_result["grade_point"] = grade.get("grade_point", 0.0) if grade else 0.0
        is_pass, score_tone = _raw_score_state(
            session_result["final_score"],
            session_result["maximum_score"],
            grade,
            legacy=session_result["attendance_allocation"] is None,
        )
        session_result["is_pass"] = is_pass
        session_result["score_tone"] = score_tone
        base_report.update(
            {
                # Compatibility aliases continue to describe the selected
                # exam session used by existing portal/report consumers.
                "annual_score": session_result["final_score"],
                "annual_maximum": session_result["maximum_score"],
                "annual_total_score": _number(annual.get("total_score")),
                "annual_total_maximum": _number(annual.get("total_maximum")),
                "annual_percentage": _number(annual.get("percentage")),
                "annual_status": annual.get("status"),
                "annual_reason": annual.get("reason"),
                "annual_session_count": len(annual.get("session_results", [])),
                "percentage": session_result["percentage"],
                "session_score": session_result["final_score"],
                "session_maximum": session_result["maximum_score"],
                "base_score": session_result["base_score"],
                "positive_points": session_result["positive_points"],
                "negative_points": session_result["negative_points"],
                "behavior_allocation": session_result["behavior_allocation"],
                "attendance_allocation": session_result["attendance_allocation"],
                "behavior_score": session_result["behavior_score"],
                "behavior_positive_points": session_result["behavior_positive_points"],
                "behavior_negative_points": session_result["behavior_negative_points"],
                "attendance_score": session_result["attendance_score"],
                "attendance_positive_points": session_result["attendance_positive_points"],
                "attendance_negative_points": session_result["attendance_negative_points"],
                "session_results": [session_result],
                "current_sessions": [session_result],
                "events": event_rows,
                "grade": grade,
                "grade_point": grade.get("grade_point", 0.0) if grade else 0.0,
                "is_pass": is_pass,
                "score_tone": score_tone,
                "behavior_status": score.get("behavior_status"),
                "behavior_reason": score.get("behavior_reason"),
                "scoring_status": score.get("scoring_status", score.get("attendance_status")),
                "scoring_reason": score.get("attendance_reason") or score.get("behavior_reason"),
                "ledger": score.get("ledger"),
            }
        )
        reports.append(base_report)
    return reports
