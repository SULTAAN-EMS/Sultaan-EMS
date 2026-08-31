"""Business rules for the Phase 2B Behavior domain.

This module is deliberately independent from ordinary examination Result and
Attendance calculations. It only reads the shared academic hierarchy and
StudentEnrollment records, then writes Behavior-owned records.
"""

from datetime import datetime
from decimal import Decimal, InvalidOperation

from . import db
from .models import (
    AcademicYear,
    AcademicYearLevel,
    AcademicYearSubject,
    BehaviorAction,
    BehaviorCategory,
    BehaviorConfiguration,
    BehaviorEvent,
    ExamType,
    Exam,
    StudentEnrollment,
)


class BehaviorValidationError(ValueError):
    """Raised when a Behavior operation crosses an academic boundary."""


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
    validate_behavior_scope(
        configuration.academic_year_id,
        configuration.academic_year_level_id,
        configuration.academic_year_subject_id,
    )
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
    if session and BehaviorEvent.query.filter_by(behavior_session_id=session.id).first():
        raise BehaviorValidationError(
            "This Behavior session has recorded events and cannot be changed because its history must remain unchanged."
        )
    return configuration


def ensure_configuration_editable(configuration):
    """Validate scope; historical event checks protect edits independently."""
    return validate_behavior_configuration(configuration)


def validate_enrollment_scope(configuration, enrollment_id):
    configuration = validate_behavior_configuration(configuration)
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


def calculate_session_score(configuration, session, enrollment):
    """Return base, separate adjustments, and clamped current score."""
    configuration = validate_behavior_configuration(configuration)
    if session.configuration is not configuration and session.behavior_configuration_id != configuration.id:
        raise BehaviorValidationError("Behavior session does not belong to the selected configuration")
    enrollment = validate_enrollment_scope(configuration, enrollment.id if hasattr(enrollment, "id") else enrollment)
    maximum = decimal_value(session.maximum_score, "Session maximum", minimum="0.001")
    base = (maximum / Decimal("2")).quantize(Decimal("0.001"))
    rows = BehaviorEvent.query.filter_by(
        behavior_configuration_id=configuration.id,
        behavior_session_id=session.id,
        student_enrollment_id=enrollment.id,
        status="active",
    ).all()
    positive_raw = sum(
        (decimal_value(row.points_applied, "Event points") for row in rows if row.polarity == "positive"),
        Decimal("0.000"),
    )
    positive_capacity = (maximum / Decimal("2")).quantize(Decimal("0.001"))
    positive_applied = min(positive_raw, positive_capacity).quantize(Decimal("0.001"))
    negative = sum(
        (decimal_value(row.points_applied, "Event points") for row in rows if row.polarity == "negative"),
        Decimal("0.000"),
    ).quantize(Decimal("0.001"))
    final = max(Decimal("0.000"), min(maximum, base + positive_applied - negative)).quantize(Decimal("0.001"))
    percentage = (final / maximum * Decimal("100")).quantize(Decimal("0.001"))
    return {
        "maximum_score": maximum,
        "base_score": base,
        "positive_capacity": positive_capacity,
        "positive_raw_points": positive_raw.quantize(Decimal("0.001")),
        "positive_applied_points": positive_applied,
        "negative_points": negative,
        "final_score": final,
        "percentage": percentage,
        # Compatibility aliases for the Phase 2B templates.
        "base": base,
        "positive": positive_applied,
        "negative": negative,
        "final": final,
        "maximum": maximum,
        "event_count": len(rows),
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
):
    """Record an event with immutable category/action/points snapshots."""
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
    points = decimal_value(action.points, "Action points", minimum="0.001")
    session_maximum = decimal_value(
        session.maximum_score,
        "Session maximum",
        minimum="0.001",
    )
    if points > session_maximum:
        raise BehaviorValidationError(
            f"Action points ({points:g}) cannot exceed the selected session maximum ({session_maximum:g})"
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
    event = BehaviorEvent(
        student_id=enrollment.student_id,
        student_enrollment_id=enrollment.id,
        behavior_configuration_id=configuration.id,
        behavior_session_id=session.id,
        behavior_category_id=category.id,
        behavior_action_id=action.id,
        polarity=category.polarity,
        points_applied=points,
        status="active",
        occurred_at=occurred_at or datetime.utcnow(),
        notes=(notes or "").strip() or None,
        category_name_snapshot=category.name,
        action_name_snapshot=action.name,
        action_level_snapshot=action.level_number,
        session_label_snapshot=session.session_label,
        idempotency_key=key,
        created_by=created_by,
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
    points = decimal_value(action.points, "Action points", minimum="0.001")
    session_maximum = decimal_value(
        session.maximum_score,
        "Session maximum",
        minimum="0.001",
    )
    if points > session_maximum:
        raise BehaviorValidationError(
            f"Action points ({points:g}) cannot exceed the selected session maximum ({session_maximum:g})"
        )
    occurred_at = occurred_at or event.occurred_at
    old_values = {
        "session_id": event.behavior_session_id,
        "category_id": event.behavior_category_id,
        "action_id": event.behavior_action_id,
        "direction": event.polarity,
        "points_applied": str(event.points_applied),
        "action_level": event.action_level_snapshot,
        "occurred_at": str(event.occurred_at),
        "notes": event.notes,
    }
    event.behavior_session_id = session.id
    event.behavior_category_id = category.id
    event.behavior_action_id = action.id
    event.polarity = direction
    event.points_applied = points
    event.action_level_snapshot = action.level_number
    event.category_name_snapshot = category.name
    event.action_name_snapshot = action.name
    event.session_label_snapshot = session.session_label
    event.occurred_at = occurred_at
    event.notes = (notes or "").strip() or None
    new_values = {
        "session_id": event.behavior_session_id,
        "category_id": event.behavior_category_id,
        "action_id": event.behavior_action_id,
        "direction": event.polarity,
        "points_applied": str(event.points_applied),
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
