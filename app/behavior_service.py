"""Business rules for the Phase 2B Behavior domain.

This module is deliberately independent from ordinary examination Result and
Attendance calculations. It only reads the shared academic hierarchy and
StudentEnrollment records, then writes Behavior-owned records.
"""

import json
from datetime import datetime
from decimal import Decimal, InvalidOperation

from . import db
from .models import (
    AcademicYear,
    AcademicYearLevel,
    AcademicYearSubject,
    BehaviorAction,
    BehaviorAttendanceStatus,
    BehaviorAttendanceRecord,
    BehaviorAttendanceDay,
    BehaviorCategory,
    BehaviorConfiguration,
    BehaviorEvent,
    ExamType,
    Exam,
    StudentEnrollment,
)


class BehaviorValidationError(ValueError):
    """Raised when a Behavior operation crosses an academic boundary."""


ATTENDANCE_SCORING_POLICY_VERSION = "attendance-normalization-v1"
CANONICAL_ATTENDANCE_STATUS_KEYS = frozenset(
    {"present", "late", "absent", "excused", "official_leave"}
)
DEFAULT_ATTENDANCE_WEIGHTS = {
    "present": Decimal("1.000"),
    "late": Decimal("-0.500"),
    "absent": Decimal("-1.000"),
    "excused": Decimal("0.000"),
    "official_leave": Decimal("0.000"),
}

CANONICAL_ATTENDANCE_POLARITIES = {
    "present": "positive",
    "late": "negative",
    "absent": "negative",
    "excused": "neutral",
    "official_leave": "neutral",
}
INTERNAL_COMPLETE_STATUSES = frozenset({"VALID", "VALID_BASELINE"})


def public_scoring_status(status):
    """Map internal scoring detail to the public Behavior status contract."""
    return "COMPLETE" if status in INTERNAL_COMPLETE_STATUSES else status


def _configured_attendance_weight(status, fallback):
    """Convert one configured status into its signed scoring weight."""
    if not status or not status.is_active:
        return fallback
    points = decimal_value(status.points, "Attendance points", minimum="0")
    if status.polarity == "positive":
        return points
    if status.polarity == "negative":
        return -points
    return Decimal("0.000")


def attendance_policy_snapshot_for_configuration(configuration):
    """Return the exact five-status policy configured for one scope.

    This is the only place that translates editable Attendance status rows into
    scoring weights.  Sessions persist the result before the first mark so
    later policy edits cannot rewrite finalized history.
    """
    statuses = {
        item.key: item
        for item in BehaviorAttendanceStatus.query.filter_by(
            behavior_configuration_id=configuration.id
        ).all()
    }
    weights = {
        key: _configured_attendance_weight(statuses.get(key), fallback)
        for key, fallback in DEFAULT_ATTENDANCE_WEIGHTS.items()
    }
    return {
        "version": ATTENDANCE_SCORING_POLICY_VERSION,
        "weights": {key: str(value.quantize(Decimal("0.001"))) for key, value in weights.items()},
        "status_keys": sorted(CANONICAL_ATTENDANCE_STATUS_KEYS),
    }


def capture_attendance_session_policy(configuration, session):
    """Snapshot status weights and calendar semantics for a new session."""
    if getattr(session, "id", None) and BehaviorAttendanceRecord.query.filter_by(
        behavior_session_id=session.id
    ).first():
        return session

    policy = attendance_policy_snapshot_for_configuration(configuration)
    session.attendance_policy_snapshot = json.dumps(policy, sort_keys=True)
    session.attendance_frequency_snapshot = (configuration.frequency or "monthly").strip().lower()
    active_days = BehaviorAttendanceDay.query.filter_by(
        behavior_configuration_id=configuration.id,
        is_active=True,
    ).order_by(BehaviorAttendanceDay.weekday).all()
    session.attendance_weekdays_snapshot = ",".join(str(item.weekday) for item in active_days)
    weights = {
        key: Decimal(value)
        for key, value in policy["weights"].items()
    }
    session.attendance_present_weight = weights["present"]
    session.attendance_late_weight = weights["late"]
    session.attendance_absent_weight = weights["absent"]
    session.scoring_policy_version = policy["version"]
    return session


# These are the only response contracts exposed to new Behavior actions.  The
# database still contains the original behavior_type column, so the adapter
# below preserves old records without allowing deprecated types into new data.
BEHAVIOR_RESPONSE_TYPES = (
    "short_answer",
    "multiple_choice",
    "checkboxes",
    "rating",
    "text_note",
)
_RESPONSE_TYPE_TO_STORAGE = {
    "short_answer": "direct_action",
    "multiple_choice": "choice",
    "checkboxes": "selection",
    "rating": "rating",
    "text_note": "text_note",
}
_STORAGE_TO_RESPONSE_TYPE = {
    **{value: key for key, value in _RESPONSE_TYPE_TO_STORAGE.items()},
    "dropdown": "multiple_choice",
    "linear_scale": "rating",
}


def canonical_response_type(action_or_type):
    """Return the public response type for an action or stored type."""
    value = getattr(action_or_type, "response_type", None) or getattr(action_or_type, "behavior_type", action_or_type)
    value = (str(value or "short_answer").strip().lower())
    return _STORAGE_TO_RESPONSE_TYPE.get(value, value)


def storage_response_type(response_type):
    """Translate a canonical response type to the legacy DB representation."""
    value = (str(response_type or "short_answer").strip().lower())
    if value not in BEHAVIOR_RESPONSE_TYPES:
        raise BehaviorValidationError(
            "Response type must be Short answer, Multiple choice, Checkboxes, Rating, or Text / note"
        )
    return _RESPONSE_TYPE_TO_STORAGE[value]


def _response_text_value(value, label="Response"):
    value = (value or "").strip()
    if len(value) > 2000:
        raise BehaviorValidationError(f"{label} cannot exceed 2000 characters")
    return value or None


def decimal_value(value, label="value", minimum=None):
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise BehaviorValidationError(f"{label} must be numeric") from exc
    if not parsed.is_finite():
        raise BehaviorValidationError(f"{label} must be finite")
    if minimum is not None and parsed < Decimal(str(minimum)):
        raise BehaviorValidationError(f"{label} must be at least {minimum}")
    return parsed.quantize(Decimal("0.001"))


def normalize_idempotency_key(value):
    key = str(value or "").strip()
    if not key:
        return None
    if len(key) > 120:
        raise BehaviorValidationError("Event submission key is too long")
    return key


def find_event_by_idempotency_key(value):
    key = normalize_idempotency_key(value)
    if not key:
        return None
    return BehaviorEvent.query.filter_by(idempotency_key=key).first()


def validate_behavior_scope(academic_year_id, academic_year_level_id, academic_year_subject_id):
    """Validate the exact Year -> Level -> Behavior Subject scope."""
    year = db.session.get(AcademicYear, academic_year_id)
    if not year:
        raise BehaviorValidationError("Academic Year does not exist")
    level = db.session.get(AcademicYearLevel, academic_year_level_id)
    if not level or level.academic_year_id != year.id:
        raise BehaviorValidationError("Academic Level does not belong to the selected Academic Year")
    subject = db.session.get(AcademicYearSubject, academic_year_subject_id)
    if not subject or subject.academic_year_id != year.id or subject.academic_year_level_id != level.id:
        raise BehaviorValidationError("Behavior Subject does not belong to the selected Academic Year and Level")
    if (getattr(subject, "subject_kind", "exam") or "exam") != "behavior":
        raise BehaviorValidationError("The selected subject is not classified as a Behavior subject")
    if not subject.is_active:
        raise BehaviorValidationError("The selected Behavior subject is not active")
    return year, level, subject


def validate_behavior_configuration(configuration):
    if not configuration:
        raise BehaviorValidationError("Behavior configuration was not found")
    # A dashboard evaluates the same configuration for every student. Keep
    # the exact-scope check once per request so a long class does not issue
    # the same three database lookups repeatedly.
    cache = db.session.info.setdefault("_behavior_validated_configuration_scopes", set())
    cache_key = (
        configuration.id,
        configuration.academic_year_id,
        configuration.academic_year_level_id,
        configuration.academic_year_subject_id,
    )
    if cache_key not in cache:
        validate_behavior_scope(
            configuration.academic_year_id,
            configuration.academic_year_level_id,
            configuration.academic_year_subject_id,
        )
        cache.add(cache_key)
    return configuration


def configuration_for_scope(academic_year_id, academic_year_level_id, academic_year_subject_id=None):
    query = BehaviorConfiguration.query.filter_by(
        academic_year_id=academic_year_id,
        academic_year_level_id=academic_year_level_id,
    )
    if academic_year_subject_id:
        query = query.filter_by(academic_year_subject_id=academic_year_subject_id)
    return query.order_by(BehaviorConfiguration.id).first()


def allocation_total(configuration):
    configuration = validate_behavior_configuration(configuration)
    total = sum(
        (decimal_value(item.maximum_score, "Session maximum", minimum="0.001") for item in configuration.sessions),
        Decimal("0.000"),
    )
    return total.quantize(Decimal("0.001"))


def refresh_allocation_total(configuration):
    configuration.annual_allocation = allocation_total(configuration)
    return configuration.annual_allocation


def validate_configuration_ready(configuration):
    """Validate the operational prerequisites without changing a lifecycle state."""
    configuration = validate_behavior_configuration(configuration)
    for session in configuration.sessions:
        validate_session_scope(
            configuration,
            exam_type_id=session.exam_type_id,
            exam_id=session.exam_id,
        )
    total = refresh_allocation_total(configuration)
    if total != Decimal("100.000"):
        raise BehaviorValidationError(
            f"Behavior sessions must total exactly 100 before events can be recorded (currently {total:g})"
        )
    if not configuration.sessions:
        raise BehaviorValidationError("At least one Behavior session is required before recording events")
    if not configuration.categories:
        raise BehaviorValidationError("At least one Behavior category is required before recording events")
    if not any(category.actions for category in configuration.categories):
        raise BehaviorValidationError("At least one Behavior action is required before recording events")
    return configuration


def validate_session_scope(configuration, exam_type_id=None, exam_id=None):
    """Validate a canonical Results Hub Exam or a legacy ExamType in scope."""
    configuration = validate_behavior_configuration(configuration)
    if exam_id is not None:
        exam = db.session.get(Exam, exam_id)
        if (
            not exam
            or exam.academic_year_id != configuration.academic_year_id
            or not exam.is_active
        ):
            raise BehaviorValidationError("Exam Type does not belong to the Behavior Academic Year")
        configured_level = configuration.academic_year_level
        if (
            exam.academic_level_id is not None
            and (
                not configured_level
                or configured_level.legacy_level_id != exam.academic_level_id
            )
        ):
            raise BehaviorValidationError("Exam Type does not belong to the Behavior Academic Year and Level")
        return exam
    exam_type = db.session.get(ExamType, exam_type_id) if exam_type_id is not None else None
    if (
        not exam_type
        or exam_type.academic_year_id != configuration.academic_year_id
        or not exam_type.is_active
    ):
        raise BehaviorValidationError("Exam Type does not belong to the Behavior Academic Year")
    return exam_type


def ensure_session_editable(configuration, session=None):
    """Prevent session changes from rewriting history-backed event snapshots."""
    configuration = validate_behavior_configuration(configuration)
    if session and (
        BehaviorEvent.query.filter_by(behavior_session_id=session.id).first()
        or BehaviorAttendanceRecord.query.filter_by(behavior_session_id=session.id).first()
    ):
        raise BehaviorValidationError(
            "This Behavior session has recorded events or Attendance history and cannot be changed because its history must remain unchanged."
        )
    return configuration


def _clamp_decimal(value, minimum, maximum):
    return max(minimum, min(maximum, value))


def _ledger_component(allocation, positive_used, negative_used, total_used, status=None):
    """Build one read-only allocation ledger for every reporting surface."""
    if allocation is None:
        projection = {
            "allocation": None,
            "positive_capacity": None,
            "negative_capacity": None,
            "positive_used": decimal_value(positive_used or 0, "Positive used"),
            "negative_used": decimal_value(negative_used or 0, "Negative used"),
            "used": decimal_value(total_used or 0, "Total used"),
            "remaining": None,
            "status": status or "LEGACY_COMPATIBILITY",
        }
        projection["positive_max"] = projection["positive_capacity"]
        projection["negative_max"] = projection["negative_capacity"]
        projection["positive_remaining"] = None
        projection["negative_remaining"] = None
        projection["earned_score"] = None
        projection["grand_total"] = None
        projection["positive_percent"] = None
        projection["negative_percent"] = None
        projection["earned_percent"] = None
        return projection
    allocation = decimal_value(allocation, "Allocation", minimum="0")
    positive_used = max(Decimal("0.000"), decimal_value(positive_used or 0, "Positive used"))
    negative_used = max(Decimal("0.000"), decimal_value(negative_used or 0, "Negative used"))
    used = max(Decimal("0.000"), decimal_value(total_used or 0, "Total used"))
    remaining = max(Decimal("0.000"), allocation - used).quantize(Decimal("0.001"))
    capacity = (allocation / Decimal("2")).quantize(Decimal("0.001"))
    projection = {
        "allocation": allocation,
        "positive_capacity": capacity,
        "negative_capacity": capacity,
        "positive_used": positive_used.quantize(Decimal("0.001")),
        "negative_used": negative_used.quantize(Decimal("0.001")),
        "used": used.quantize(Decimal("0.001")),
        "remaining": remaining,
        "status": status or ("NOT_APPLICABLE" if allocation == 0 else "AVAILABLE"),
    }
    projection["positive_max"] = projection["positive_capacity"]
    projection["negative_max"] = projection["negative_capacity"]
    projection["positive_remaining"] = max(
        Decimal("0.000"), capacity - projection["positive_used"]
    ).quantize(Decimal("0.001"))
    projection["negative_remaining"] = max(
        Decimal("0.000"), capacity - projection["negative_used"]
    ).quantize(Decimal("0.001"))
    projection["earned_score"] = projection["used"]
    projection["grand_total"] = projection["earned_score"]
    projection["positive_percent"] = (
        (projection["positive_used"] / capacity * Decimal("100")).quantize(Decimal("0.1"))
        if capacity else Decimal("0.0")
    )
    projection["negative_percent"] = (
        (projection["negative_used"] / capacity * Decimal("100")).quantize(Decimal("0.1"))
        if capacity else Decimal("0.0")
    )
    projection["earned_percent"] = (
        (projection["earned_score"] / allocation * Decimal("100")).quantize(Decimal("0.1"))
        if allocation else Decimal("0.0")
    )
    return projection


def _attendance_ledger_component(allocation, positive_used, negative_used, total_used, status=None):
    """Build the single-total Attendance ledger.

    Attendance allocation is one maximum for the normalized result.  It is not
    split into independent positive and negative half-capacities; those values
    are evidence contributions used to explain the final score.
    """
    if allocation is None:
        return _ledger_component(allocation, positive_used, negative_used, total_used, status)
    allocation = decimal_value(allocation, "Attendance allocation", minimum="0")
    positive_used = max(Decimal("0.000"), decimal_value(positive_used or 0, "Attendance positive contribution"))
    negative_used = max(Decimal("0.000"), decimal_value(negative_used or 0, "Attendance negative contribution"))
    used = max(Decimal("0.000"), decimal_value(total_used or 0, "Attendance earned score"))
    status = status or ("NOT_APPLICABLE" if allocation == 0 else "AVAILABLE")
    incomplete = status == "INCOMPLETE"
    projection = {
        "allocation": allocation,
        "positive_capacity": None,
        "negative_capacity": None,
        "positive_used": None if incomplete else positive_used.quantize(Decimal("0.001")),
        "negative_used": None if incomplete else negative_used.quantize(Decimal("0.001")),
        "used": None if incomplete else used.quantize(Decimal("0.001")),
        "remaining": None if incomplete else max(Decimal("0.000"), allocation - used).quantize(Decimal("0.001")),
        "status": status,
        "positive_max": None,
        "negative_max": None,
        "positive_remaining": None,
        "negative_remaining": None,
        "earned_score": None if incomplete else used.quantize(Decimal("0.001")),
        "grand_total": None if incomplete else used.quantize(Decimal("0.001")),
        "positive_percent": None,
        "negative_percent": None,
        "earned_percent": (
            (used / allocation * Decimal("100")).quantize(Decimal("0.1"))
            if allocation and not incomplete else None
        ),
    }
    return projection


def session_allocation_projection(session):
    """Return the canonical, student-independent allocation contract.

    A session created before the allocation columns existed remains a legacy
    session.  It is reported explicitly instead of being silently rewritten.
    """
    if not session:
        raise BehaviorValidationError("Behavior session was not found")
    maximum = decimal_value(session.maximum_score, "Session maximum", minimum="0.001")
    behavior_value = getattr(session, "behavior_allocation", None)
    attendance_value = getattr(session, "attendance_allocation", None)
    if behavior_value is None and attendance_value is None:
        return {
            "session_maximum": maximum,
            "behavior_allocation": None,
            "attendance_allocation": None,
            "total_allocation": None,
            "is_valid": True,
            "status": "LEGACY_COMPATIBILITY",
            "behavior_positive_max": None,
            "behavior_negative_max": None,
            "attendance_positive_max": None,
            "attendance_negative_max": None,
        }
    if behavior_value is None or attendance_value is None:
        raise BehaviorValidationError(
            "Behavior and Attendance allocations must either both be set or both be empty"
        )
    behavior = decimal_value(behavior_value, "Behavior allocation", minimum="0")
    attendance = decimal_value(attendance_value, "Attendance allocation", minimum="0")
    total = (behavior + attendance).quantize(Decimal("0.001"))
    if total != maximum:
        raise BehaviorValidationError(
            "Behavior and Attendance allocations must equal the session maximum"
        )
    return {
        "session_maximum": maximum,
        "behavior_allocation": behavior,
        "attendance_allocation": attendance,
        "total_allocation": total,
        "is_valid": True,
        "status": "NOT_APPLICABLE" if total == 0 else "AVAILABLE",
        "behavior_positive_max": (behavior / Decimal("2")).quantize(Decimal("0.001")),
        "behavior_negative_max": (behavior / Decimal("2")).quantize(Decimal("0.001")),
        # Attendance has one total maximum; positive/negative are evidence
        # contributions, not separately allocated capacities.
        "attendance_positive_max": None,
        "attendance_negative_max": None,
    }


def scoring_ledger_projection(score):
    """Return the single allocation/used/remaining projection shared by reports."""
    behavior = _ledger_component(
        score.get("behavior_allocation"),
        score.get("behavior_positive_points"),
        score.get("behavior_negative_points"),
        score.get("behavior_score"),
        score.get("scoring_status"),
    )
    attendance = _attendance_ledger_component(
        score.get("attendance_allocation"),
        score.get("attendance_positive_points"),
        score.get("attendance_negative_points"),
        score.get("attendance_score"),
        score.get("attendance_status"),
    )
    if attendance.get("status") == "INCOMPLETE":
        for key in (
            "positive_used",
            "negative_used",
            "used",
            "remaining",
            "positive_remaining",
            "negative_remaining",
            "earned_score",
            "grand_total",
        ):
            attendance[key] = None
    maximum = score.get("maximum_score")
    final = score.get("final_score")
    session_maximum = decimal_value(maximum, "Session maximum", minimum="0.001") if maximum is not None else None
    grand_total = decimal_value(final, "Session grand total") if final is not None else None
    session_remaining = (
        max(Decimal("0.000"), session_maximum - grand_total).quantize(Decimal("0.001"))
        if session_maximum is not None and grand_total is not None else None
    )
    session = {
        "session_maximum": session_maximum,
        "behavior_allocation": score.get("behavior_allocation"),
        "attendance_allocation": score.get("attendance_allocation"),
        "behavior_earned": score.get("behavior_score"),
        "attendance_earned": score.get("attendance_score"),
        "grand_total": grand_total,
        "session_remaining": session_remaining,
        "status": score.get("scoring_status"),
        # Compatibility aliases for older consumers that expect a ledger shape.
        "allocation": session_maximum,
        "used": grand_total,
        "remaining": session_remaining,
    }
    return {"behavior": behavior, "attendance": attendance, "session": session}


def validate_behavior_ledger_capacity(configuration, session, enrollment, polarity, points, exclude_event_id=None):
    """Reject a normalized event before it can exceed its polarity capacity."""
    allocation = getattr(session, "behavior_allocation", None)
    if allocation is None:
        return
    points = decimal_value(points, "Behavior points", minimum="0")
    if points == 0 or polarity not in {"positive", "negative"}:
        return
    capacity = (decimal_value(allocation, "Behavior allocation", minimum="0") / Decimal("2")).quantize(Decimal("0.001"))
    query = BehaviorEvent.query.filter_by(
        behavior_configuration_id=configuration.id,
        behavior_session_id=session.id,
        student_enrollment_id=enrollment.id,
        polarity=polarity,
        status="active",
    )
    if exclude_event_id is not None:
        query = query.filter(BehaviorEvent.id != exclude_event_id)
    used = sum(
        (
            decimal_value(row.points_applied, "Event points")
            for row in query.all()
        ),
        Decimal("0.000"),
    )
    if used + points > capacity:
        remaining = max(Decimal("0.000"), capacity - used).quantize(Decimal("0.001"))
        raise BehaviorValidationError(
            f"Only {remaining:g} {polarity} Behavior point(s) remain for this session; "
            f"the selected response requires {points:g}."
        )


def attendance_policy_for_session(session):
    """Return the immutable Attendance policy captured by a session."""
    snapshot = getattr(session, "attendance_policy_snapshot", None)
    if snapshot:
        try:
            payload = json.loads(snapshot)
            snapshot_weights = payload.get("weights") or {}
            weights = {
                key: decimal_value(snapshot_weights.get(key), f"Attendance {key} weight")
                for key in CANONICAL_ATTENDANCE_STATUS_KEYS
                if snapshot_weights.get(key) is not None
            }
            if set(weights) == set(CANONICAL_ATTENDANCE_STATUS_KEYS):
                return {
                    "version": payload.get("version") or ATTENDANCE_SCORING_POLICY_VERSION,
                    "weights": weights,
                }
        except (TypeError, ValueError, json.JSONDecodeError):
            # Older or malformed snapshots use the explicit compatibility path
            # below rather than making a report impossible to render.
            pass
    values = {
        "present": getattr(session, "attendance_present_weight", None),
        "late": getattr(session, "attendance_late_weight", None),
        "absent": getattr(session, "attendance_absent_weight", None),
    }
    weights = {
        key: decimal_value(value, f"Attendance {key} weight")
        if value is not None else DEFAULT_ATTENDANCE_WEIGHTS[key]
        for key, value in values.items()
    }
    return {
        "version": getattr(session, "scoring_policy_version", None) or "legacy",
        "weights": {
            **DEFAULT_ATTENDANCE_WEIGHTS,
            **weights,
        },
    }


def attendance_points_projection(records):
    """Aggregate saved Attendance points exactly once using their snapshots.

    Attendance records persist both the configured polarity and the point value
    that was applied at mark time.  Every report and scoring surface must use
    those immutable snapshots rather than re-reading a later status policy or
    combining a second query.  The identity guard also makes this projection
    safe when a caller accidentally supplies the same ORM row more than once.
    """
    seen = set()
    positive = Decimal("0.000")
    negative = Decimal("0.000")
    record_count = 0
    for row in records or ():
        enrollment_id = getattr(row, "student_enrollment_id", None)
        session_id = getattr(row, "behavior_session_id", None)
        attendance_date = getattr(row, "attendance_date", None)
        if enrollment_id is not None and session_id is not None and attendance_date is not None:
            identity = ("scope", enrollment_id, session_id, attendance_date)
        else:
            identity = ("id", getattr(row, "id", None), id(row))
        if identity in seen:
            continue
        seen.add(identity)
        status_key = (getattr(row, "status_key_snapshot", None) or "").strip().lower()
        if status_key not in CANONICAL_ATTENDANCE_STATUS_KEYS:
            continue
        record_count += 1
        points = abs(decimal_value(getattr(row, "points_applied", 0) or 0, "Attendance points"))
        # Attendance polarity is defined by the five official status keys.  A
        # legacy row may still contain the old positive Late snapshot; using
        # the canonical key here prevents that row from inflating positives.
        polarity = CANONICAL_ATTENDANCE_POLARITIES.get(
            status_key,
            (getattr(row, "polarity", None) or "neutral").strip().lower(),
        )
        if polarity == "positive":
            positive += points
        elif polarity == "negative":
            negative += points
    positive = positive.quantize(Decimal("0.001"))
    negative = negative.quantize(Decimal("0.001"))
    return {
        "positive_points": positive,
        "negative_points": negative,
        "signed_total": (positive - negative).quantize(Decimal("0.001")),
        "record_count": record_count,
    }


def attendance_score_projection(
    configuration,
    session,
    enrollment,
    *,
    _validated=False,
    attendance_records=None,
):
    """Calculate direct configured Attendance points for one session/student.

    This is the sole Attendance scoring implementation. Saved status points
    are summed directly and clamped to the single Attendance allocation. No
    period, day, opportunity, ratio, or allocation-half normalization applies.
    """
    if not _validated:
        configuration = validate_behavior_configuration(configuration)
        enrollment = validate_enrollment_scope(
            configuration, enrollment.id if hasattr(enrollment, "id") else enrollment
        )
    if not session or session.behavior_configuration_id != configuration.id:
        raise BehaviorValidationError("Attendance session is outside the selected Behavior configuration")

    # A NULL allocation identifies a pre-contract session. Its historical
    # behavior remains available through the legacy compatibility calculation.
    allocation_value = getattr(session, "attendance_allocation", None)
    if allocation_value is None:
        return {
            "status": "LEGACY_COMPATIBILITY",
            "public_status": "LEGACY_COMPATIBILITY",
            "reason": "This session predates the normalized Attendance scoring contract.",
            "attendance_score": None,
            "record_count": 0,
            "policy_version": "legacy",
        }

    allocation = decimal_value(allocation_value, "Attendance allocation", minimum="0")
    if attendance_records is None:
        rows = BehaviorAttendanceRecord.query.filter_by(
            behavior_configuration_id=configuration.id,
            behavior_session_id=session.id,
            student_enrollment_id=enrollment.id,
        ).order_by(
            BehaviorAttendanceRecord.attendance_date.asc(),
            BehaviorAttendanceRecord.id.asc(),
        ).all()
    else:
        rows = list(attendance_records)
    canonical_rows = [
        row for row in rows
        if (row.status_key_snapshot or "").strip().lower() in CANONICAL_ATTENDANCE_STATUS_KEYS
    ]
    point_projection = attendance_points_projection(canonical_rows)
    policy = attendance_policy_for_session(session)
    if allocation == 0:
        return {
            "status": "NOT_APPLICABLE",
            "public_status": "NOT_APPLICABLE",
            "reason": "This session has no Attendance allocation.",
            "attendance_score": Decimal("0.000"),
            "allocation": allocation,
            "base_score": Decimal("0.000"),
            "positive_capacity": None,
            "negative_capacity": None,
            "positive_evidence": Decimal("0.000"),
            "negative_evidence": Decimal("0.000"),
            "positive_applied": Decimal("0.000"),
            "negative_applied": Decimal("0.000"),
            "record_count": point_projection["record_count"],
            "policy_version": policy["version"],
        }
    if not canonical_rows:
        return {
            "status": "INCOMPLETE",
            "public_status": "INCOMPLETE",
            "reason": "No canonical Attendance record exists for this valid session and student.",
            "attendance_score": None,
            "allocation": allocation,
            "base_score": Decimal("0.000"),
            "record_count": 0,
            "policy_version": policy["version"],
        }

    counted_rows = [
        row for row in canonical_rows
        if (row.status_key_snapshot or "").strip().lower()
        in {"present", "late", "absent"}
    ]
    positive_evidence = point_projection["positive_points"]
    negative_evidence = point_projection["negative_points"]
    positive_applied = positive_evidence
    negative_applied = negative_evidence
    configured_total = (positive_evidence - negative_evidence).quantize(Decimal("0.001"))
    score = _clamp_decimal(configured_total, Decimal("0.000"), allocation)
    status = "VALID_BASELINE" if not counted_rows else "VALID"
    return {
        "status": status,
        "public_status": public_scoring_status(status),
        "reason": "No non-neutral Attendance points were recorded." if not counted_rows else None,
        "attendance_score": score,
        "allocation": allocation,
        "base_score": Decimal("0.000"),
        "positive_capacity": None,
        "negative_capacity": None,
        "positive_evidence": positive_evidence,
        "negative_evidence": negative_evidence,
        "positive_applied": positive_applied,
        "negative_applied": negative_applied,
        "record_count": point_projection["record_count"],
        "policy_version": policy["version"],
    }


def ensure_configuration_editable(configuration):
    """Validate scope; historical event checks protect edits independently."""
    return validate_behavior_configuration(configuration)


def validate_enrollment_scope(configuration, enrollment_id):
    configuration = validate_behavior_configuration(configuration)
    enrollment_id = enrollment_id.id if hasattr(enrollment_id, "id") else enrollment_id
    enrollment = db.session.get(StudentEnrollment, enrollment_id)
    if not enrollment:
        raise BehaviorValidationError("Student enrollment was not found")
    if (
        enrollment.academic_year_id != configuration.academic_year_id
        or enrollment.academic_year_level_id != configuration.academic_year_level_id
    ):
        raise BehaviorValidationError("Student enrollment does not belong to the selected Behavior scope")
    if (
        not enrollment.academic_year_level
        or enrollment.academic_year_level.academic_year_id != configuration.academic_year_id
        or not enrollment.academic_year_class
        or enrollment.academic_year_class.academic_year_level_id != configuration.academic_year_level_id
    ):
        raise BehaviorValidationError("Student enrollment class does not belong to the selected Behavior scope")
    if enrollment.status in {"withdrawn", "archived"}:
        raise BehaviorValidationError("Only an active or completed enrollment can receive a Behavior event")
    return enrollment


def calculate_session_score(
    configuration,
    session,
    enrollment,
    *,
    behavior_events=None,
    attendance_records=None,
):
    """Return one session score projection for Behavior and Attendance."""
    configuration = validate_behavior_configuration(configuration)
    if session.configuration is not configuration and session.behavior_configuration_id != configuration.id:
        raise BehaviorValidationError("Behavior session does not belong to the selected configuration")
    enrollment = validate_enrollment_scope(configuration, enrollment.id if hasattr(enrollment, "id") else enrollment)
    maximum = decimal_value(session.maximum_score, "Session maximum", minimum="0.001")
    explicit_behavior_allocation = getattr(session, "behavior_allocation", None)
    explicit_attendance_allocation = getattr(session, "attendance_allocation", None)
    legacy_scoring = explicit_behavior_allocation is None or explicit_attendance_allocation is None
    if legacy_scoring:
        behavior_allocation = maximum
        attendance_allocation = None
    else:
        behavior_allocation = decimal_value(
            explicit_behavior_allocation, "Behavior allocation", minimum="0"
        )
        attendance_allocation = decimal_value(
            explicit_attendance_allocation, "Attendance allocation", minimum="0"
        )
        if (behavior_allocation + attendance_allocation).quantize(Decimal("0.001")) != maximum:
            raise BehaviorValidationError(
                "Behavior and Attendance allocations must equal the session maximum"
            )
    base = (behavior_allocation / Decimal("2")).quantize(Decimal("0.001"))
    if behavior_events is None:
        rows = BehaviorEvent.query.filter_by(
            behavior_configuration_id=configuration.id,
            behavior_session_id=session.id,
            student_enrollment_id=enrollment.id,
            status="active",
        ).all()
    else:
        rows = [row for row in behavior_events if row.status == "active"]
    positive_raw = sum(
        (decimal_value(row.points_applied, "Event points") for row in rows if row.polarity == "positive"),
        Decimal("0.000"),
    )
    positive_capacity = (behavior_allocation / Decimal("2")).quantize(Decimal("0.001"))
    positive_applied = min(positive_raw, positive_capacity).quantize(Decimal("0.001"))
    negative_raw = sum(
        (decimal_value(row.points_applied, "Event points") for row in rows if row.polarity == "negative"),
        Decimal("0.000"),
    )
    negative_raw = negative_raw.quantize(Decimal("0.001"))
    negative_capacity = positive_capacity
    negative_applied = min(negative_raw, negative_capacity).quantize(Decimal("0.001"))
    behavior_score = max(
        Decimal("0.000"),
        min(behavior_allocation, (base + positive_applied - negative_applied).quantize(Decimal("0.001"))),
    )
    # Both scope checks have already completed above. Avoid repeating them in
    # the Attendance projection for every student on a dashboard request.
    attendance = attendance_score_projection(
        configuration,
        session,
        enrollment,
        _validated=True,
        attendance_records=attendance_records,
    )
    if legacy_scoring:
        # Preserve the old calculation for sessions created before allocations
        # existed; this avoids silently rewriting historical results.
        from .behavior_attendance import attendance_score_adjustments

        legacy_attendance = attendance_score_adjustments(
            configuration,
            session,
            enrollment,
            attendance_records=attendance_records,
        )
        positive_raw += legacy_attendance["positive_points"]
        positive_applied = min(positive_raw, positive_capacity).quantize(Decimal("0.001"))
        negative_raw += legacy_attendance["negative_points"]
        negative_applied = negative_raw.quantize(Decimal("0.001"))
        behavior_score = max(Decimal("0.000"), min(maximum, base + positive_applied - negative_applied)).quantize(Decimal("0.001"))
        final = behavior_score
        attendance_status = "LEGACY_COMPATIBILITY"
        attendance_score = None
        attendance_positive_applied = legacy_attendance["positive_points"]
        attendance_negative_applied = legacy_attendance["negative_points"]
        attendance_record_count = legacy_attendance["record_count"]
    else:
        attendance_status = attendance["status"]
        attendance_score = attendance.get("attendance_score")
        attendance_positive_applied = attendance.get("positive_applied", Decimal("0"))
        attendance_negative_applied = attendance.get("negative_applied", Decimal("0"))
        attendance_record_count = attendance.get("record_count", 0)
        # An allocated but unrecorded Attendance component makes the session
        # incomplete; it must never be mistaken for Absent or Present.
        final = (
            None
            if attendance_status == "INCOMPLETE"
            else (behavior_score + (attendance_score or Decimal("0"))).quantize(Decimal("0.001"))
        )
        if final is not None:
            final = max(Decimal("0.000"), min(maximum, final)).quantize(Decimal("0.001"))
    percentage = (
        (final / maximum * Decimal("100")).quantize(Decimal("0.001"))
        if final is not None else None
    )
    result = {
        "maximum_score": maximum,
        "behavior_allocation": behavior_allocation,
        "attendance_allocation": attendance_allocation,
        "base_score": base,
        "positive_capacity": positive_capacity,
        "positive_raw_points": positive_raw.quantize(Decimal("0.001")),
        "positive_applied_points": positive_applied,
        "behavior_positive_points": positive_applied,
        "behavior_negative_points": negative_applied,
        "behavior_positive_capacity": positive_capacity,
        "behavior_negative_capacity": negative_capacity,
        "behavior_score": behavior_score,
        "attendance_positive_points": attendance_positive_applied,
        "attendance_negative_points": attendance_negative_applied,
        "attendance_record_count": attendance_record_count,
        "attendance_score": attendance_score,
        "attendance_status": attendance_status,
        "scoring_status": public_scoring_status(attendance_status),
        "attendance_reason": attendance.get("reason"),
        "attendance": attendance,
        "negative_points": negative_applied,
        "final_score": final,
        "percentage": percentage,
        # Compatibility aliases for the Phase 2B templates.
        "base": base,
        "positive": positive_applied,
        "negative": negative_applied,
        "final": final,
        "maximum": maximum,
        "event_count": len(rows),
        # Reuse the canonical active rows in dashboard callers instead of
        # issuing a second identical BehaviorEvent query per student.
        "_active_events": rows,
    }
    result["ledger"] = scoring_ledger_projection(result)
    return result


def calculate_annual_behavior_score(configuration, enrollment):
    """Aggregate all active Behavior sessions without averaging sessions.

    Each session is calculated by ``calculate_session_score`` first. The
    annual percentage is then the earned-score sum divided by the applicable
    maximum sum, so a 15-point session and an 18-point session carry their
    configured weights instead of being treated as equal-sized averages.
    """
    configuration = validate_behavior_configuration(configuration)
    enrollment = validate_enrollment_scope(
        configuration, enrollment.id if hasattr(enrollment, "id") else enrollment
    )
    sessions = sorted(
        [item for item in configuration.sessions if item.is_active],
        key=lambda item: (item.sort_order, item.id),
    )
    if not sessions:
        return {
            "status": "NOT_APPLICABLE",
            "reason": "No active Behavior sessions are configured.",
            "total_score": Decimal("0.000"),
            "total_maximum": Decimal("0.000"),
            "percentage": None,
            "session_results": [],
        }

    session_results = []
    total_score = Decimal("0.000")
    total_maximum = Decimal("0.000")
    for session in sessions:
        score = calculate_session_score(configuration, session, enrollment)
        session_results.append(score)
        if score.get("attendance_status") == "INCOMPLETE" or score.get("final_score") is None:
            return {
                "status": "INCOMPLETE",
                "reason": score.get("attendance_reason") or "One or more Behavior sessions are incomplete.",
                "total_score": None,
                "total_maximum": total_maximum,
                "percentage": None,
                "session_results": session_results,
            }
        total_score += decimal_value(score["final_score"], "Session score")
        total_maximum += decimal_value(score["maximum_score"], "Session maximum")

    total_score = total_score.quantize(Decimal("0.001"))
    total_maximum = total_maximum.quantize(Decimal("0.001"))
    if total_maximum <= 0:
        return {
            "status": "NOT_APPLICABLE",
            "reason": "No applicable Behavior maximum exists for this enrollment.",
            "total_score": Decimal("0.000"),
            "total_maximum": total_maximum,
            "percentage": None,
            "session_results": session_results,
        }
    total_score = max(Decimal("0.000"), min(total_maximum, total_score))
    return {
        "status": "COMPLETE",
        "reason": None,
        "total_score": total_score,
        "total_maximum": total_maximum,
        "percentage": (total_score / total_maximum * Decimal("100")).quantize(Decimal("0.001")),
        "session_results": session_results,
    }


def _resolve_behavior_response(action, choice=None, choices=None, response_text=None, rating=None):
    """Validate one response contract and return its canonical point snapshot."""
    response_type = canonical_response_type(action)
    if response_type not in BEHAVIOR_RESPONSE_TYPES:
        raise BehaviorValidationError("This Behavior action has an unsupported response type")

    submitted_choices = list(choices or [])
    if choice is not None and choice not in submitted_choices:
        submitted_choices.insert(0, choice)
    if response_type == "multiple_choice":
        if len(submitted_choices) != 1:
            raise BehaviorValidationError("Select exactly one response option")
    elif response_type == "checkboxes":
        if not submitted_choices:
            raise BehaviorValidationError("Select at least one response option")
    elif submitted_choices:
        raise BehaviorValidationError("Response options are not valid for this action")
    if submitted_choices:
        if any(item is None for item in submitted_choices):
            raise BehaviorValidationError("Behavior response option was not found")
        if any(
            item.behavior_action_id != action.id or not item.is_active
            for item in submitted_choices
        ):
            raise BehaviorValidationError("Behavior response option is outside the selected action")
        if len({item.id for item in submitted_choices}) != len(submitted_choices):
            raise BehaviorValidationError("A response option cannot be selected more than once")

    action_points = decimal_value(action.points, "Action points", minimum="0.001")
    response_text = _response_text_value(response_text, "Action response")
    response_rating_value = None
    response_rating_scale = None
    if response_type in {"short_answer", "text_note"}:
        if action.response_required and not response_text:
            raise BehaviorValidationError("A response is required for this action")
        response_points = action_points
    elif response_type == "multiple_choice":
        response_points = decimal_value(submitted_choices[0].points, "Response option points", minimum="0")
    elif response_type == "checkboxes":
        response_points = sum(
            (decimal_value(item.points, "Response option points", minimum="0") for item in submitted_choices),
            Decimal("0.000"),
        ).quantize(Decimal("0.001"))
        if response_points > action_points:
            raise BehaviorValidationError("Combined checkbox points cannot exceed the action maximum")
    else:  # rating
        try:
            response_rating_scale = int(action.rating_scale or 0)
        except (TypeError, ValueError) as exc:
            raise BehaviorValidationError("Rating scale is invalid") from exc
        if response_rating_scale not in {5, 7, 8, 10}:
            raise BehaviorValidationError("Rating scale must be 5, 7, 8, or 10")
        try:
            response_rating_value = int(rating)
        except (TypeError, ValueError) as exc:
            raise BehaviorValidationError("Select a rating") from exc
        if not 1 <= response_rating_value <= response_rating_scale:
            raise BehaviorValidationError("Rating is outside the configured scale")
        response_points = (action_points * Decimal(response_rating_value) / Decimal(response_rating_scale)).quantize(Decimal("0.001"))

    if response_points < 0 or response_points > action_points:
        raise BehaviorValidationError(
            f"This response exceeds the Action Maximum of {action_points:g} points."
        )
    selected_labels = [item.label for item in submitted_choices]
    if response_type == "rating":
        response_display = f"{response_rating_value}/{response_rating_scale} stars"
    elif response_text:
        response_display = response_text
    else:
        response_display = ", ".join(selected_labels)
    response_snapshot = {
        "response_type": response_type,
        "selected_options": [
            {"id": item.id, "label": item.label, "points": str(item.points)}
            for item in submitted_choices
        ],
        "rating": response_rating_value,
        "rating_scale": response_rating_scale,
        "text": response_text,
        "action_points": str(action_points),
        "response_points": str(response_points),
    }
    return {
        "response_type": response_type,
        "submitted_choices": submitted_choices,
        "selected_labels": selected_labels,
        "action_points": action_points,
        "response_points": response_points,
        "response_text": response_text,
        "response_rating": response_rating_value,
        "response_rating_scale": response_rating_scale,
        "response_display": response_display,
        "response_snapshot": response_snapshot,
    }


def record_event(
    configuration,
    enrollment,
    session,
    category,
    action,
    notes=None,
    occurred_at=None,
    created_by=None,
    direction=None,
    idempotency_key=None,
    choice=None,
    choices=None,
    response_text=None,
    rating=None,
):
    """Record an event with immutable taxonomy and response snapshots."""
    configuration = validate_behavior_configuration(configuration)
    enrollment = validate_enrollment_scope(configuration, enrollment.id if hasattr(enrollment, "id") else enrollment)
    if not session or session.behavior_configuration_id != configuration.id:
        raise BehaviorValidationError("Behavior session does not belong to the selected configuration")
    validate_session_scope(
        configuration,
        exam_type_id=session.exam_type_id,
        exam_id=session.exam_id,
    )
    if category.behavior_configuration_id != configuration.id:
        raise BehaviorValidationError("Behavior category does not belong to the selected configuration")
    if action.behavior_category_id != category.id:
        raise BehaviorValidationError("Behavior action does not belong to the selected category")
    direction = (direction or category.polarity).strip().lower()
    if direction != category.polarity:
        raise BehaviorValidationError("Event direction must match the selected category")
    response = _resolve_behavior_response(
        action,
        choice=choice,
        choices=choices,
        response_text=response_text,
        rating=rating,
    )
    response_type = response["response_type"]
    submitted_choices = response["submitted_choices"]
    selected_labels = response["selected_labels"]
    action_points = response["action_points"]
    response_points = response["response_points"]
    response_text = response["response_text"]
    response_rating_value = response["response_rating"]
    response_rating_scale = response["response_rating_scale"]
    response_display = response["response_display"]
    response_snapshot = response["response_snapshot"]
    session_maximum = decimal_value(
        session.maximum_score,
        "Session maximum",
        minimum="0.001",
    )
    if response_points > session_maximum:
        raise BehaviorValidationError(
            f"Response points ({response_points:g}) cannot exceed the selected session maximum ({session_maximum:g})"
        )
    key = normalize_idempotency_key(idempotency_key)
    if key:
        existing = find_event_by_idempotency_key(key)
        if existing:
            if (
                existing.student_enrollment_id != enrollment.id
                or existing.behavior_configuration_id != configuration.id
                or existing.behavior_session_id != session.id
            ):
                raise BehaviorValidationError("Event submission key was already used for another event")
            return existing
    validate_behavior_ledger_capacity(
        configuration,
        session,
        enrollment,
        direction,
        response_points,
    )
    event = BehaviorEvent(
        student_id=enrollment.student_id,
        student_enrollment_id=enrollment.id,
        behavior_configuration_id=configuration.id,
        behavior_session_id=session.id,
        behavior_category_id=category.id,
        behavior_action_id=action.id,
        polarity=category.polarity,
        points_applied=response_points,
        status="active",
        occurred_at=occurred_at or datetime.utcnow(),
        notes=(notes or "").strip() or None,
        category_name_snapshot=category.name,
        action_name_snapshot=(
            f"{action.name}: {selected_labels[0]}"
            if not getattr(action, "response_type", None) and len(selected_labels) == 1
            else action.name
        ),
        action_level_snapshot=action.level_number,
        subcategory_name_snapshot=action.subcategory.name if action.subcategory else None,
        session_label_snapshot=session.session_label,
        idempotency_key=key,
        created_by=created_by,
        behavior_action_choice_id=(submitted_choices[0].id if len(submitted_choices) == 1 else None),
        response_type_snapshot=response_type,
        response_text=response_text,
        response_snapshot=json.dumps(response_snapshot, ensure_ascii=False, sort_keys=True),
        response_display_snapshot=response_display,
        response_points=response_points,
        action_points_snapshot=action_points,
        response_rating=response_rating_value,
        response_rating_scale=response_rating_scale,
    )
    db.session.add(event)
    return event


def edit_event(
    event,
    configuration,
    enrollment,
    session,
    category,
    action,
    direction=None,
    occurred_at=None,
    notes=None,
    reason=None,
    choice=None,
    choices=None,
    response_text=None,
    rating=None,
):
    """Edit an active event in place only with an explicit audit reason."""
    if not event or event.status != "active":
        raise BehaviorValidationError("Only a recorded Behavior event can be edited")
    reason = (reason or "").strip()
    if not reason:
        raise BehaviorValidationError("A reason is required when editing a Behavior event")
    configuration = validate_behavior_configuration(configuration)
    enrollment = validate_enrollment_scope(configuration, enrollment.id if hasattr(enrollment, "id") else enrollment)
    if (
        event.behavior_configuration_id != configuration.id
        or event.student_enrollment_id != enrollment.id
    ):
        raise BehaviorValidationError("Behavior event is outside the selected configuration or enrollment")
    if not session or session.behavior_configuration_id != configuration.id:
        raise BehaviorValidationError("Behavior session does not belong to the selected configuration")
    validate_session_scope(
        configuration,
        exam_type_id=session.exam_type_id,
        exam_id=session.exam_id,
    )
    if category.behavior_configuration_id != configuration.id:
        raise BehaviorValidationError("Behavior category does not belong to the selected configuration")
    if action.behavior_category_id != category.id:
        raise BehaviorValidationError("Behavior action does not belong to the selected category")
    direction = (direction or category.polarity).strip().lower()
    if direction != category.polarity:
        raise BehaviorValidationError("Event direction must match the selected category")
    response = _resolve_behavior_response(
        action,
        choice=choice,
        choices=choices,
        response_text=response_text,
        rating=rating,
    )
    response_type = response["response_type"]
    submitted_choices = response["submitted_choices"]
    selected_labels = response["selected_labels"]
    action_points = response["action_points"]
    response_points = response["response_points"]
    response_text = response["response_text"]
    response_rating_value = response["response_rating"]
    response_rating_scale = response["response_rating_scale"]
    response_display = response["response_display"]
    response_snapshot = response["response_snapshot"]
    session_maximum = decimal_value(
        session.maximum_score,
        "Session maximum",
        minimum="0.001",
    )
    if response_points > session_maximum:
        raise BehaviorValidationError(
            f"Response points ({response_points:g}) cannot exceed the selected session maximum ({session_maximum:g})"
        )
    validate_behavior_ledger_capacity(
        configuration,
        session,
        enrollment,
        direction,
        response_points,
        exclude_event_id=event.id,
    )
    occurred_at = occurred_at or event.occurred_at
    old_values = {
        "session_id": event.behavior_session_id,
        "category_id": event.behavior_category_id,
        "action_id": event.behavior_action_id,
        "direction": event.polarity,
        "points_applied": str(event.points_applied),
        "response_points": str(event.response_points) if event.response_points is not None else None,
        "response_display": event.response_display_snapshot,
        "response_snapshot": event.response_snapshot,
        "action_level": event.action_level_snapshot,
        "occurred_at": str(event.occurred_at),
        "notes": event.notes,
    }
    event.behavior_session_id = session.id
    event.behavior_category_id = category.id
    event.behavior_action_id = action.id
    event.polarity = direction
    event.points_applied = response_points
    event.action_level_snapshot = action.level_number
    event.category_name_snapshot = category.name
    event.action_name_snapshot = action.name
    event.session_label_snapshot = session.session_label
    event.occurred_at = occurred_at
    event.notes = (notes or "").strip() or None
    event.behavior_action_choice_id = submitted_choices[0].id if len(submitted_choices) == 1 else None
    event.response_type_snapshot = response_type
    event.response_text = response_text
    event.response_snapshot = json.dumps(response_snapshot, ensure_ascii=False, sort_keys=True)
    event.response_display_snapshot = response_display
    event.response_points = response_points
    event.action_points_snapshot = action_points
    event.response_rating = response_rating_value
    event.response_rating_scale = response_rating_scale
    new_values = {
        "session_id": event.behavior_session_id,
        "category_id": event.behavior_category_id,
        "action_id": event.behavior_action_id,
        "direction": event.polarity,
        "points_applied": str(event.points_applied),
        "response_points": str(event.response_points),
        "response_display": event.response_display_snapshot,
        "response_snapshot": event.response_snapshot,
        "action_level": event.action_level_snapshot,
        "occurred_at": str(event.occurred_at),
        "notes": event.notes,
        "reason": reason,
    }
    return event, old_values, new_values


def void_event(event, voided_by, reason):
    if not event or event.status != "active":
        raise BehaviorValidationError("Only an active Behavior event can be voided")
    reason = (reason or "").strip()
    if not reason:
        raise BehaviorValidationError("A reason is required when voiding a Behavior event")
    event.status = "voided"
    event.voided_by = voided_by
    event.voided_at = datetime.utcnow()
    event.void_reason = reason
    return event


def restore_event(event):
    """Restore a voided event without changing its historical snapshots."""
    if not event or event.status != "voided":
        raise BehaviorValidationError("Only a voided Behavior event can be restored")
    event.status = "active"
    event.voided_by = None
    event.voided_at = None
    event.void_reason = None
    return event


def behavior_summary(configuration):
    configuration = validate_behavior_configuration(configuration)
    active_events = BehaviorEvent.query.filter_by(
        behavior_configuration_id=configuration.id,
        status="active",
    )
    return {
        "sessions": len(configuration.sessions),
        "categories": len(configuration.categories),
        "actions": BehaviorAction.query.join(BehaviorCategory).filter(
            BehaviorCategory.behavior_configuration_id == configuration.id,
        ).count(),
        "active_events": active_events.count(),
        "voided_events": BehaviorEvent.query.filter_by(
            behavior_configuration_id=configuration.id,
            status="voided",
        ).count(),
        "allocation": refresh_allocation_total(configuration),
    }
