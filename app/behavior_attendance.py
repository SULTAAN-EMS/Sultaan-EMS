"""Attendance foundation owned by the Behavior domain.

Daily Attendance uses the same year-aware configuration, session, and
enrollment boundaries as Behavior events.  It intentionally does not reuse
the examination-hall AttendanceRecord table, whose lifecycle is different.
"""

from datetime import date, datetime, time
from decimal import Decimal

from . import db
from .behavior_service import (
    CANONICAL_ATTENDANCE_STATUS_KEYS,
    BehaviorValidationError,
    attendance_points_projection,
    decimal_value,
    capture_attendance_session_policy,
    validate_behavior_configuration,
    validate_enrollment_scope,
    validate_session_scope,
)
from .models import (
    BehaviorAttendanceDay,
    BehaviorAttendanceRecord,
    BehaviorAttendanceStatus,
    BehaviorConfiguration,
    BehaviorSession,
    StudentEnrollment,
)


DEFAULT_ATTENDANCE_STATUSES = (
    {"key": "present", "label": "Joogid", "polarity": "positive", "points": "1", "sort_order": 1},
    {"key": "late", "label": "Daahid", "polarity": "negative", "points": "0.5", "sort_order": 2},
    {"key": "absent", "label": "Maqnaansho", "polarity": "negative", "points": "1", "sort_order": 3},
    {"key": "excused", "label": "Cudurdaar", "polarity": "neutral", "points": "0", "sort_order": 4},
    {"key": "official_leave", "label": "Fasaxid Rasmi ah", "polarity": "neutral", "points": "0", "sort_order": 5},
)

OFFICIAL_ATTENDANCE_LABELS = {
    "present": "Joogid",
    "late": "Daahid",
    "absent": "Maqnaansho",
    "excused": "Cudurdaar",
    "official_leave": "Fasaxid Rasmi ah",
}


def attendance_status_label(status_key, fallback=None):
    """Return one stable display label without rewriting historical records."""
    key = (status_key or "").strip().lower()
    return OFFICIAL_ATTENDANCE_LABELS.get(key, fallback or key.replace("_", " ").title())


def _coerce_time(value):
    if value in (None, ""):
        return None
    if isinstance(value, time):
        return value.replace(second=0, microsecond=0)
    try:
        return datetime.strptime(str(value).strip(), "%H:%M").time()
    except (TypeError, ValueError):
        raise BehaviorValidationError("Arrival time must use HH:MM format")

# Python's weekday numbering is Monday=0.  Saturday through Thursday is the
# useful default for the school's normal six-day week; administrators can
# deactivate any day without changing the stored attendance history.
DEFAULT_ATTENDANCE_DAYS = (
    (5, "Saturday"),
    (6, "Sunday"),
    (0, "Monday"),
    (1, "Tuesday"),
    (2, "Wednesday"),
    (3, "Thursday"),
)


def ensure_attendance_defaults(configuration):
    """Create missing status/day defaults without overwriting admin changes."""
    configuration = validate_behavior_configuration(configuration)
    existing_statuses = {
        item.key
        for item in BehaviorAttendanceStatus.query.filter_by(
            behavior_configuration_id=configuration.id
        ).all()
    }
    for values in DEFAULT_ATTENDANCE_STATUSES:
        if values["key"] not in existing_statuses:
            db.session.add(
                BehaviorAttendanceStatus(
                    behavior_configuration_id=configuration.id,
                    key=values["key"],
                    label=values["label"],
                    polarity=values["polarity"],
                    points=Decimal(values["points"]),
                    sort_order=values["sort_order"],
                    contributes_to_behavior=True,
                    is_active=True,
                )
            )
    # Late is always a negative Attendance status.  Correct an older default
    # in-place without touching saved record snapshots or unrelated statuses.
    late_status = BehaviorAttendanceStatus.query.filter_by(
        behavior_configuration_id=configuration.id,
        key="late",
    ).first()
    if late_status and late_status.polarity != "negative":
        late_status.polarity = "negative"
    # Preserve historical Emergency rows for audit, but prevent the retired
    # status from being offered as a canonical new Attendance status.
    emergency = BehaviorAttendanceStatus.query.filter_by(
        behavior_configuration_id=configuration.id,
        key="emergency",
    ).first()
    if emergency:
        emergency.is_active = False
    existing_days = {
        item.weekday
        for item in BehaviorAttendanceDay.query.filter_by(
            behavior_configuration_id=configuration.id
        ).all()
    }
    for weekday, label in DEFAULT_ATTENDANCE_DAYS:
        if weekday not in existing_days:
            db.session.add(
                BehaviorAttendanceDay(
                    behavior_configuration_id=configuration.id,
                    weekday=weekday,
                    label=label,
                    is_active=True,
                )
            )
    db.session.flush()
    return configuration


def attendance_statuses(configuration, active_only=True):
    configuration = validate_behavior_configuration(configuration)
    query = BehaviorAttendanceStatus.query.filter_by(
        behavior_configuration_id=configuration.id
    ).order_by(BehaviorAttendanceStatus.sort_order, BehaviorAttendanceStatus.id)
    if active_only:
        query = query.filter_by(is_active=True)
    return query.all()


def attendance_days(configuration, active_only=True):
    configuration = validate_behavior_configuration(configuration)
    query = BehaviorAttendanceDay.query.filter_by(
        behavior_configuration_id=configuration.id
    ).order_by(BehaviorAttendanceDay.weekday)
    if active_only:
        query = query.filter_by(is_active=True)
    return query.all()


def validate_attendance_context(configuration, session, enrollment):
    """Reject cross-year, cross-level, wrong-session, and wrong-class writes."""
    configuration = validate_behavior_configuration(configuration)
    enrollment = validate_enrollment_scope(configuration, enrollment)
    if not session or session.behavior_configuration_id != configuration.id:
        raise BehaviorValidationError("Attendance session is outside the selected Behavior configuration")
    validate_session_scope(
        configuration,
        exam_type_id=session.exam_type_id,
        exam_id=session.exam_id,
    )
    if enrollment.academic_year_class_id is None:
        raise BehaviorValidationError("Student enrollment has no Academic Year Class")
    return configuration, session, enrollment


def validate_attendance_ledger_capacity(configuration, session, enrollment, polarity, points, exclude_record_id=None):
    """Validate a record without treating daily rows as score capacity.

    Attendance allocation is a normalized total for the whole opportunity
    period.  It must not reject valid daily records after an old ``allocation /
    2`` positive/negative bucket fills; the canonical projection applies the
    final cap after normalizing all applicable rows.
    """
    if polarity not in {"positive", "negative", "neutral"}:
        raise BehaviorValidationError("Attendance status has an invalid polarity")
    decimal_value(points or 0, "Attendance points", minimum="0")


def _snapshot_status_points_for_new_session(configuration, session):
    """Capture current admin status points before the first Attendance mark.

    Sessions with existing Attendance history keep their original snapshot so
    a later policy edit cannot silently rewrite historical scores.
    """
    capture_attendance_session_policy(configuration, session)


def enrollments_for_class(configuration, academic_year_class_id=None):
    """Return only active/completed enrollments in the exact Behavior scope."""
    configuration = validate_behavior_configuration(configuration)
    query = StudentEnrollment.query.filter(
        StudentEnrollment.academic_year_id == configuration.academic_year_id,
        StudentEnrollment.academic_year_level_id == configuration.academic_year_level_id,
        StudentEnrollment.status.in_(("active", "completed")),
    )
    if academic_year_class_id:
        query = query.filter(StudentEnrollment.academic_year_class_id == int(academic_year_class_id))
    return query.order_by(StudentEnrollment.id).all()


def generate_daily_roster(configuration, session, attendance_date, academic_year_class_id=None, attendance_time=None):
    """Materialize one Present row per enrollment for a configured school day."""
    configuration = validate_behavior_configuration(configuration)
    if not session or session.behavior_configuration_id != configuration.id:
        raise BehaviorValidationError("Attendance session is outside the selected Behavior configuration")
    validate_session_scope(
        configuration,
        exam_type_id=session.exam_type_id,
        exam_id=session.exam_id,
    )
    if not isinstance(attendance_date, date):
        raise BehaviorValidationError("Attendance date is invalid")
    if attendance_date.weekday() not in {item.weekday for item in attendance_days(configuration)}:
        raise BehaviorValidationError("The selected date is not configured as a school attendance day")
    present = next((item for item in attendance_statuses(configuration) if item.key == "present"), None)
    if not present:
        raise BehaviorValidationError("A Present Attendance status is required")
    created = 0
    for enrollment in enrollments_for_class(configuration, academic_year_class_id):
        existing = BehaviorAttendanceRecord.query.filter_by(
            student_enrollment_id=enrollment.id,
            behavior_session_id=session.id,
            attendance_date=attendance_date,
        ).first()
        if existing:
            continue
        validate_attendance_ledger_capacity(
            configuration, session, enrollment, present.polarity,
            present.points if present.contributes_to_behavior else 0,
        )
        db.session.add(
            BehaviorAttendanceRecord(
                student_id=enrollment.student_id,
                student_enrollment_id=enrollment.id,
                behavior_configuration_id=configuration.id,
                behavior_session_id=session.id,
                academic_year_id=configuration.academic_year_id,
                academic_year_level_id=configuration.academic_year_level_id,
                academic_year_class_id=enrollment.academic_year_class_id,
                attendance_date=attendance_date,
                attendance_time=_coerce_time(attendance_time) or datetime.now().time().replace(microsecond=0),
                status_id=present.id,
                status_key_snapshot=present.key,
                status_label_snapshot=attendance_status_label(present.key, present.label),
                polarity=present.polarity,
                points_applied=present.points if present.contributes_to_behavior else 0,
            )
        )
        created += 1
    db.session.flush()
    return created


def mark_attendance(
    configuration,
    session,
    enrollment,
    status_id,
    attendance_date,
    note=None,
    marked_by_id=None,
    attendance_time=None,
    arrival_time=None,
    late_by_minutes=None,
):
    """Upsert exactly one daily mark while preserving the selected scope."""
    configuration, session, enrollment = validate_attendance_context(configuration, session, enrollment)
    if not isinstance(attendance_date, date):
        raise BehaviorValidationError("Attendance date is invalid")
    status = db.session.get(BehaviorAttendanceStatus, status_id)
    if not status or status.behavior_configuration_id != configuration.id or not status.is_active:
        raise BehaviorValidationError("Attendance status is outside the selected Behavior configuration")
    status_key = (status.key or "").strip().lower()
    if status_key not in CANONICAL_ATTENDANCE_STATUS_KEYS:
        raise BehaviorValidationError("Only the five official Attendance statuses can be recorded")
    _snapshot_status_points_for_new_session(configuration, session)
    item = BehaviorAttendanceRecord.query.filter_by(
        student_enrollment_id=enrollment.id,
        behavior_session_id=session.id,
        attendance_date=attendance_date,
    ).first()
    if item and item.status == "voided":
        raise BehaviorValidationError(
            "This Attendance record is voided and immutable. Restore it before editing."
        )
    normalized_arrival_time = _coerce_time(arrival_time) if status_key == "late" else None
    if status_key == "late" and normalized_arrival_time is None:
        raise BehaviorValidationError("Arrival time is required when status is Late")
    new_polarity = "negative" if status_key == "late" else status.polarity
    new_points = status.points if status.contributes_to_behavior else Decimal("0")
    validate_attendance_ledger_capacity(
        configuration,
        session,
        enrollment,
        new_polarity,
        new_points,
        exclude_record_id=item.id if item else None,
    )
    if not item:
        item = BehaviorAttendanceRecord(
            student_id=enrollment.student_id,
            student_enrollment_id=enrollment.id,
            behavior_configuration_id=configuration.id,
            behavior_session_id=session.id,
            academic_year_id=configuration.academic_year_id,
            academic_year_level_id=configuration.academic_year_level_id,
            academic_year_class_id=enrollment.academic_year_class_id,
            attendance_date=attendance_date,
        )
        db.session.add(item)
    item.status_id = status.id
    item.status_key_snapshot = status_key
    item.status_label_snapshot = attendance_status_label(status_key, status.label)
    item.polarity = new_polarity
    item.points_applied = new_points
    item.note = (note or "").strip() or None
    item.marked_by_id = marked_by_id
    item.attendance_time = _coerce_time(attendance_time) or datetime.now().time().replace(microsecond=0)
    item.arrival_time = normalized_arrival_time
    item.late_by_minutes = int(late_by_minutes) if status_key == "late" and late_by_minutes not in (None, "") else None
    db.session.flush()
    return item


def attendance_score_adjustments(
    configuration,
    session,
    enrollment,
    *,
    attendance_records=None,
):
    """Return Attendance points using the same positive/negative score inputs."""
    configuration, session, enrollment = validate_attendance_context(configuration, session, enrollment)
    if not configuration.behavior_attendance_scoring_enabled:
        return {
            "positive_points": Decimal("0.000"),
            "negative_points": Decimal("0.000"),
            "record_count": 0,
        }
    if attendance_records is None:
        rows = BehaviorAttendanceRecord.query.filter_by(
            behavior_configuration_id=configuration.id,
            behavior_session_id=session.id,
            student_enrollment_id=enrollment.id,
        ).all()
    else:
        rows = list(attendance_records)
    projection = attendance_points_projection(rows)
    return {
        "positive_points": projection["positive_points"],
        "negative_points": projection["negative_points"],
        "record_count": projection["record_count"],
    }
