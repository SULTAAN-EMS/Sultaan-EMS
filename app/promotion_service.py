"""Phase 3B promotion-rule configuration and evaluation foundation.

The evaluator is deliberately independent from enrollment transitions.  It
creates evidence snapshots and never changes ``StudentEnrollment`` state.
"""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal, InvalidOperation

from . import db
from .models import (
    AcademicYear,
    AcademicYearLevel,
    AcademicYearSubject,
    Exam,
    PromotionEvaluation,
    PromotionRule,
    PromotionRuleCriticalSubject,
    Setting,
    StudentEnrollment,
)
from .services import get_settings


PROMOTION_RULES_SETTING_KEY = "promotion_rules_enabled"
DEFAULT_PROMOTION_THRESHOLD = Decimal("50")


class PromotionValidationError(ValueError):
    """Raised when a promotion rule or evaluation is outside its scope."""


def _decimal(value, *, field, default=None):
    if value in (None, "") and default is not None:
        value = default
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise PromotionValidationError(f"{field} must be a number")
    if result < 0 or result > 100:
        raise PromotionValidationError(f"{field} must be between 0 and 100")
    return result.quantize(Decimal("0.001"))


def _json_default(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat() + "Z"
    raise TypeError(f"Unsupported JSON value: {type(value)!r}")


def promotion_rules_enabled(settings=None):
    """Return the global toggle without requiring a database row to exist."""
    settings = settings or get_settings()
    return str(settings.get(PROMOTION_RULES_SETTING_KEY, "false")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def set_promotion_rules_enabled(enabled):
    """Persist the global toggle and return its normalized boolean value."""
    value = "true" if bool(enabled) else "false"
    setting = db.session.get(Setting, PROMOTION_RULES_SETTING_KEY)
    if setting is None:
        setting = Setting(key=PROMOTION_RULES_SETTING_KEY, value=value)
        db.session.add(setting)
    else:
        setting.value = value
    db.session.flush()
    return bool(enabled)


def validate_rule_scope(academic_year_id, academic_year_level_id):
    """Resolve a year-aware level and reject cross-year combinations."""
    year = db.session.get(AcademicYear, academic_year_id)
    level = db.session.get(AcademicYearLevel, academic_year_level_id)
    if not year:
        raise PromotionValidationError("Academic Year is required")
    if not level or level.academic_year_id != year.id:
        raise PromotionValidationError("Academic Year Level does not belong to the selected Academic Year")
    return year, level


def valid_critical_subjects(academic_year_id, academic_year_level_id):
    """Return only active subjects belonging to the selected year-level."""
    validate_rule_scope(academic_year_id, academic_year_level_id)
    return (
        AcademicYearSubject.query
        .filter_by(
            academic_year_id=academic_year_id,
            academic_year_level_id=academic_year_level_id,
            is_active=True,
        )
        .order_by(AcademicYearSubject.sort_order, AcademicYearSubject.name, AcademicYearSubject.id)
        .all()
    )


def _validate_critical_subject_ids(academic_year_id, academic_year_level_id, subject_ids):
    valid_subjects = valid_critical_subjects(academic_year_id, academic_year_level_id)
    valid_by_id = {subject.id: subject for subject in valid_subjects}
    normalized = []
    for raw_id in subject_ids or ():
        try:
            subject_id = int(raw_id)
        except (TypeError, ValueError):
            raise PromotionValidationError("Critical Subject selection is invalid")
        if subject_id not in valid_by_id:
            raise PromotionValidationError("Critical Subject does not belong to the selected Academic Year and Level")
        if subject_id in normalized:
            raise PromotionValidationError("Duplicate critical subject selection is not allowed")
        normalized.append(subject_id)
    return [valid_by_id[subject_id] for subject_id in normalized]


def get_promotion_rule(academic_year_id, academic_year_level_id, *, active_only=False):
    """Load a rule only from its exact year-aware scope."""
    validate_rule_scope(academic_year_id, academic_year_level_id)
    query = PromotionRule.query.filter_by(
        academic_year_id=academic_year_id,
        academic_year_level_id=academic_year_level_id,
    )
    if active_only:
        query = query.filter_by(is_active=True)
    rule = query.first()
    if rule and rule.academic_year_level.academic_year_id != rule.academic_year_id:
        raise PromotionValidationError("Promotion Rule scope is internally inconsistent")
    return rule


def upsert_promotion_rule(
    academic_year_id,
    academic_year_level_id,
    *,
    is_active=True,
    overall_pass_threshold=DEFAULT_PROMOTION_THRESHOLD,
    critical_subject_pass_threshold=DEFAULT_PROMOTION_THRESHOLD,
    critical_subject_ids=None,
):
    """Create/update one rule while validating every year-aware relationship."""
    validate_rule_scope(academic_year_id, academic_year_level_id)
    overall_threshold = _decimal(
        overall_pass_threshold,
        field="Overall PASS Threshold",
        default=DEFAULT_PROMOTION_THRESHOLD,
    )
    critical_threshold = _decimal(
        critical_subject_pass_threshold,
        field="Critical Subject PASS Threshold",
        default=DEFAULT_PROMOTION_THRESHOLD,
    )
    subjects = _validate_critical_subject_ids(
        academic_year_id,
        academic_year_level_id,
        critical_subject_ids,
    )
    rule = get_promotion_rule(academic_year_id, academic_year_level_id)
    if rule is None:
        rule = PromotionRule(
            academic_year_id=academic_year_id,
            academic_year_level_id=academic_year_level_id,
        )
        db.session.add(rule)
        db.session.flush()
    rule.is_active = bool(is_active)
    rule.overall_pass_threshold = overall_threshold
    rule.critical_subject_pass_threshold = critical_threshold
    rule.critical_subjects = [
        PromotionRuleCriticalSubject(academic_year_subject_id=subject.id)
        for subject in subjects
    ]
    db.session.flush()
    return rule


def promotion_rule_snapshot(rule, *, enabled):
    """Serialize the mutable rule into a historical-safe JSON payload."""
    if rule is None:
        return {
            "feature_enabled": bool(enabled),
            "rule_id": None,
            "overall_pass_threshold": float(DEFAULT_PROMOTION_THRESHOLD),
            "critical_subject_pass_threshold": float(DEFAULT_PROMOTION_THRESHOLD),
            "critical_subjects": [],
        }
    return {
        "feature_enabled": bool(enabled),
        "rule_id": rule.id,
        "academic_year_id": rule.academic_year_id,
        "academic_year_level_id": rule.academic_year_level_id,
        "is_active": bool(rule.is_active),
        "overall_pass_threshold": float(rule.overall_pass_threshold),
        "critical_subject_pass_threshold": float(rule.critical_subject_pass_threshold),
        "critical_subjects": [
            {
                "id": item.academic_year_subject_id,
                "name": item.academic_year_subject.name,
                "max_score": float(item.academic_year_subject.max_score),
            }
            for item in rule.critical_subjects
        ],
    }


def _normalize_subject_results(subject_results, *, academic_year_id, academic_year_level_id):
    normalized = {}
    for raw in subject_results or ():
        if not isinstance(raw, dict):
            raise PromotionValidationError("Subject evaluation data must be an object")
        subject_id = raw.get("academic_year_subject_id")
        try:
            subject_id = int(subject_id)
        except (TypeError, ValueError):
            raise PromotionValidationError("Subject evaluation requires a valid AcademicYearSubject")
        subject = db.session.get(AcademicYearSubject, subject_id)
        if (
            not subject
            or subject.academic_year_id != academic_year_id
            or subject.academic_year_level_id != academic_year_level_id
        ):
            raise PromotionValidationError("AcademicYearSubject does not belong to the evaluation scope")
        if subject_id in normalized:
            raise PromotionValidationError("Duplicate subject evaluation is not allowed")
        is_uf = bool(raw.get("is_uf"))
        percentage = None
        if not is_uf and raw.get("percentage") not in (None, ""):
            try:
                percentage = Decimal(str(raw.get("percentage")))
            except (InvalidOperation, TypeError, ValueError):
                raise PromotionValidationError("Subject percentage must be a number")
            if percentage < 0 or percentage > 100:
                raise PromotionValidationError("Subject percentage must be between 0 and 100")
        normalized[subject_id] = {
            "academic_year_subject_id": subject_id,
            "name": subject.name,
            "percentage": percentage,
            "is_uf": is_uf,
        }
    return normalized


def evaluate_promotion(student_enrollment, evaluation_context, *, persist=True):
    """Evaluate and optionally persist one year-aware promotion snapshot.

    ``evaluation_context`` must provide an explicit ``overall_percentage``.
    An optional ``exam_id`` is validated against the selected academic year;
    no latest-exam or final-exam inference is performed.
    """
    if not isinstance(student_enrollment, StudentEnrollment):
        raise PromotionValidationError("A StudentEnrollment is required")
    if not isinstance(evaluation_context, dict):
        raise PromotionValidationError("Evaluation context must be an object")
    year, level = validate_rule_scope(
        student_enrollment.academic_year_id,
        student_enrollment.academic_year_level_id,
    )
    if student_enrollment.academic_year_level_id != level.id:
        raise PromotionValidationError("Enrollment level does not match evaluation scope")
    exam_id = evaluation_context.get("exam_id")
    if exam_id not in (None, ""):
        exam = db.session.get(Exam, exam_id)
        if not exam or exam.academic_year_id != year.id:
            raise PromotionValidationError("Exam does not belong to the evaluation Academic Year")

    overall_percentage = _decimal(
        evaluation_context.get("overall_percentage"),
        field="Overall percentage",
    )
    subject_results = _normalize_subject_results(
        evaluation_context.get("subject_results", []),
        academic_year_id=year.id,
        academic_year_level_id=level.id,
    )

    enabled = promotion_rules_enabled()
    rule = get_promotion_rule(year.id, level.id, active_only=True) if enabled else None
    overall_threshold = (
        Decimal(str(rule.overall_pass_threshold))
        if rule
        else DEFAULT_PROMOTION_THRESHOLD
    )
    base_outcome = "PASS" if overall_percentage >= overall_threshold else "FAIL"
    critical_threshold = (
        Decimal(str(rule.critical_subject_pass_threshold))
        if rule
        else DEFAULT_PROMOTION_THRESHOLD
    )
    critical_results = []
    failed_critical_subjects = []
    if rule:
        for mapping in rule.critical_subjects:
            subject = mapping.academic_year_subject
            result = subject_results.get(subject.id)
            if not result or result["is_uf"] or result["percentage"] is None:
                status = "MG/UF" if result and result["is_uf"] else "NOT_EVALUATED"
                percentage = None
            else:
                percentage = result["percentage"]
                status = "PASS" if percentage >= critical_threshold else "FAIL"
                if status == "FAIL":
                    failed_critical_subjects.append(subject.id)
            critical_results.append({
                "academic_year_subject_id": subject.id,
                "subject": subject.name,
                "percentage": percentage,
                "threshold": critical_threshold,
                "status": status,
            })

    final_outcome = "FAIL" if failed_critical_subjects else base_outcome
    override_reason = "FAILED_CRITICAL_SUBJECT" if failed_critical_subjects else None
    context_snapshot = dict(evaluation_context)
    context_snapshot["academic_year_id"] = year.id
    context_snapshot["academic_year_level_id"] = level.id
    context_snapshot["student_enrollment_id"] = student_enrollment.id
    context_snapshot["overall_percentage"] = overall_percentage
    context_snapshot["evaluated_at"] = datetime.utcnow()
    snapshot = PromotionEvaluation(
        student_id=student_enrollment.student_id,
        student_enrollment_id=student_enrollment.id,
        academic_year_id=year.id,
        academic_year_level_id=level.id,
        promotion_rule_id=rule.id if rule else None,
        promotion_rule_snapshot_json=json.dumps(
            promotion_rule_snapshot(rule, enabled=enabled),
            default=_json_default,
            sort_keys=True,
        ),
        evaluation_context_json=json.dumps(context_snapshot, default=_json_default, sort_keys=True),
        overall_percentage=overall_percentage,
        base_outcome=base_outcome,
        final_outcome=final_outcome,
        critical_subject_results_json=json.dumps(critical_results, default=_json_default, sort_keys=True),
        override_reason=override_reason,
        evaluated_at=datetime.utcnow(),
    )
    if persist:
        db.session.add(snapshot)
        db.session.flush()
    return snapshot
