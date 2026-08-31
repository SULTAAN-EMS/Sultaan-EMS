"""The isolated grading authority for the Behavior domain.

Behavior grades are raw-score bands owned by one Behavior session.  This
module intentionally does not import or query the ordinary ``GradeScale``
model, so no global grade fallback can leak into Behavior reports.
"""

from decimal import Decimal, InvalidOperation

from .models import BehaviorConfiguration, BehaviorGradeScale, BehaviorSession


class BehaviorGradeValidationError(ValueError):
    """Raised when a Behavior grade scale is invalid or out of scope."""


def _decimal(value, label):
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise BehaviorGradeValidationError(f"{label} must be numeric") from exc
    if not parsed.is_finite():
        raise BehaviorGradeValidationError(f"{label} must be finite")
    return parsed.quantize(Decimal("0.001"))


def _grade_colors(is_pass):
    if is_pass:
        return {
            "badge_color": "#16a34a",
            "text_color": "#ffffff",
            "background_color": "#dcfce7",
            "border_color": "#22c55e",
        }
    return {
        "badge_color": "#dc2626",
        "text_color": "#ffffff",
        "background_color": "#fee2e2",
        "border_color": "#ef4444",
    }


def _not_configured(message="No active Behavior grade band covers this score."):
    return {
        "id": None,
        "grade": "NOT CONFIGURED",
        "min_score": None,
        "max_score": None,
        "grade_point": 0.0,
        "description": message,
        "is_active": False,
        "is_pass": False,
        **_grade_colors(False),
    }


def behavior_grade_payload(scale):
    """Return one JSON/template-safe Behavior-owned grade payload."""
    payload = {
        "id": scale.id,
        "grade": scale.grade,
        "min_score": float(scale.min_score or 0),
        "max_score": float(scale.max_score or 0),
        "grade_point": float(scale.grade_point or 0),
        "description": scale.description or "",
        "is_active": bool(scale.is_active),
        "is_pass": bool(scale.is_pass),
        "behavior_session_id": scale.behavior_session_id,
    }
    payload.update(_grade_colors(payload["is_pass"]))
    return payload


def behavior_grade_scales(scope, active_only=False):
    """Load scales for exactly one session, with legacy read compatibility.

    A new Behavior scale is always session-owned.  Configuration-only rows
    from the first Behavior release are returned only when the caller passes a
    configuration, allowing old records to remain readable without allowing
    them to override a session-owned scale.
    """
    if isinstance(scope, BehaviorSession):
        query = BehaviorGradeScale.query.filter_by(behavior_session_id=scope.id)
    elif isinstance(scope, BehaviorConfiguration):
        query = BehaviorGradeScale.query.filter(
            BehaviorGradeScale.behavior_configuration_id == scope.id,
            BehaviorGradeScale.behavior_session_id.is_(None),
        )
    else:
        return []
    if active_only:
        query = query.filter_by(is_active=True)
    return query.order_by(
        BehaviorGradeScale.sort_order,
        BehaviorGradeScale.min_score,
        BehaviorGradeScale.id,
    ).all()


def validate_behavior_grade_values(
    grade,
    min_score,
    max_score,
    grade_point,
    description=None,
    sort_order=0,
    session_maximum=None,
):
    """Validate a raw-score band, bounded by its session maximum."""
    grade = (grade or "").strip()
    if not grade:
        raise BehaviorGradeValidationError("Behavior grade letter is required")
    if len(grade) > 20:
        raise BehaviorGradeValidationError("Behavior grade letter is too long")
    minimum = _decimal(min_score, "Minimum score")
    maximum = _decimal(max_score, "Maximum score")
    point = _decimal(grade_point, "Behavior grade point")
    allowed_maximum = (
        _decimal(session_maximum, "Session maximum")
        if session_maximum is not None
        else Decimal("100.000")
    )
    if minimum < 0 or maximum < 0 or minimum > maximum:
        raise BehaviorGradeValidationError(
            "Behavior grade scores must be non-negative, with minimum not above maximum"
        )
    if maximum > allowed_maximum:
        raise BehaviorGradeValidationError(
            f"Behavior grade maximum cannot exceed the session maximum of {allowed_maximum:g}"
        )
    if point < 0:
        raise BehaviorGradeValidationError("Behavior grade point cannot be negative")
    try:
        order = int(sort_order or 0)
    except (TypeError, ValueError) as exc:
        raise BehaviorGradeValidationError("Ordering must be numeric") from exc
    return {
        "grade": grade,
        "min_score": minimum,
        "max_score": maximum,
        "grade_point": point,
        "description": (description or "").strip() or None,
        "sort_order": order,
    }


def validate_behavior_grade_overlap(scope, minimum, maximum, exclude_id=None):
    """Prevent ambiguous active raw-score bands inside one session."""
    for existing in behavior_grade_scales(scope, active_only=True):
        if exclude_id and existing.id == exclude_id:
            continue
        existing_min = Decimal(str(existing.min_score))
        existing_max = Decimal(str(existing.max_score))
        if existing_min <= maximum and existing_max >= minimum:
            raise BehaviorGradeValidationError(
                f"Behavior grade range overlaps the active {existing.grade} range"
            )


def behavior_grade_readiness(scope):
    """Describe whether active bands cover every raw score in a session."""
    if isinstance(scope, BehaviorSession):
        upper_bound = _decimal(scope.maximum_score, "Session maximum")
        scope_label = f"0 through {upper_bound:g}"
    elif isinstance(scope, BehaviorConfiguration):
        upper_bound = Decimal("100.000")
        scope_label = "0 through 100"
    else:
        return {"ready": False, "message": "Select a Behavior session first."}

    scales = behavior_grade_scales(scope, active_only=True)
    if not scales:
        return {"ready": False, "message": "No active Behavior grade bands are configured."}

    cursor = Decimal("0.000")
    for scale in scales:
        minimum = Decimal(str(scale.min_score))
        maximum = Decimal(str(scale.max_score))
        if minimum > cursor + Decimal("0.001"):
            return {
                "ready": False,
                "message": f"Behavior grade bands have a gap between {cursor:g} and {minimum:g}.",
            }
        cursor = max(cursor, maximum)
    if cursor < upper_bound:
        return {
            "ready": False,
            "message": f"Behavior grade bands stop at {cursor:g}; extend them to {scope_label}.",
        }
    return {"ready": True, "message": f"Behavior grade bands cover {scope_label}."}


def behavior_grade_for_score(session, score):
    """Resolve exactly one active Behavior grade from a raw session score."""
    if not isinstance(session, BehaviorSession):
        return _not_configured("A Behavior session is required for grade resolution.")
    try:
        value = _decimal(score, "Behavior score")
        maximum = _decimal(session.maximum_score, "Session maximum")
    except BehaviorGradeValidationError as exc:
        return _not_configured(str(exc))
    if value < 0 or value > maximum:
        return {
            "id": None,
            "grade": "INVALID",
            "min_score": None,
            "max_score": None,
            "grade_point": 0.0,
            "description": f"Behavior score must be between 0 and {maximum:g}.",
            "is_active": False,
            "is_pass": False,
            **_grade_colors(False),
        }
    matches = [
        scale
        for scale in behavior_grade_scales(session, active_only=True)
        if Decimal(str(scale.min_score)) <= value <= Decimal(str(scale.max_score))
    ]
    if len(matches) == 1:
        return behavior_grade_payload(matches[0])
    if len(matches) > 1:
        return {
            "id": None,
            "grade": "INVALID",
            "min_score": None,
            "max_score": None,
            "grade_point": 0.0,
            "description": "Behavior grade bands overlap for this score.",
            "is_active": False,
            "is_pass": False,
            **_grade_colors(False),
        }
    return _not_configured()


def behavior_grade_for_percentage(configuration, percentage):
    """Compatibility adapter for pre-session Behavior rows only.

    This function never queries ordinary GradeScale. New reporting code uses
    ``behavior_grade_for_score`` directly so the selected session remains the
    sole source of the maximum and grade range.
    """
    legacy_scales = behavior_grade_scales(configuration, active_only=True)
    if legacy_scales:
        try:
            value = _decimal(percentage, "Behavior percentage")
        except BehaviorGradeValidationError as exc:
            return _not_configured(str(exc))
        matches = [
            scale for scale in legacy_scales
            if Decimal(str(scale.min_score)) <= value <= Decimal(str(scale.max_score))
        ]
        if len(matches) == 1:
            return behavior_grade_payload(matches[0])
        if len(matches) > 1:
            return {
                "id": None,
                "grade": "INVALID",
                "min_score": None,
                "max_score": None,
                "grade_point": 0.0,
                "description": "Behavior grade bands overlap for this percentage.",
                "is_active": False,
                "is_pass": False,
                **_grade_colors(False),
            }
        return _not_configured()

    sessions = [item for item in configuration.sessions if item.is_active] if configuration else []
    if len(sessions) != 1:
        return _not_configured("Select a Behavior session to resolve its grade.")
    try:
        raw_score = _decimal(percentage, "Behavior percentage") * _decimal(
            sessions[0].maximum_score, "Session maximum"
        ) / Decimal("100")
    except BehaviorGradeValidationError as exc:
        return _not_configured(str(exc))
    return behavior_grade_for_score(sessions[0], raw_score)
