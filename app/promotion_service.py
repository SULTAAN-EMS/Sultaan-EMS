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
    AcademicYearClass,
    AcademicYearLevel,
    AcademicYearSubject,
    Exam,
    PromotionEvaluation,
    PromotionRule,
    PromotionRuleCriticalSubject,
    Result,
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
    # Phase 3C callers provide an explicit exam context and let the engine
    # derive the overall result from published Result rows.  The old explicit
    # percentage form remains supported for Phase 3B callers/tests.
    if evaluation_context.get("exam_id") not in (None, "") and evaluation_context.get("overall_percentage") in (None, ""):
        return evaluate_student_promotion(student_enrollment, evaluation_context, persist=persist)

    year, level = validate_rule_scope(
        student_enrollment.academic_year_id,
        student_enrollment.academic_year_level_id,
    )
    if student_enrollment.academic_year_level_id != level.id:
        raise PromotionValidationError("Enrollment level does not match evaluation scope")
    exam_id = evaluation_context.get("exam_id")
    exam = None
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
        exam_id=int(exam_id) if exam_id not in (None, "") else None,
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
        evaluation_status="EVALUATED",
        critical_subject_results_json=json.dumps(critical_results, default=_json_default, sort_keys=True),
        override_reason=override_reason,
        evaluated_at=datetime.utcnow(),
    )
    # Attach relationships for read-only preview rendering before a transient
    # snapshot has been flushed and reloaded by SQLAlchemy.
    snapshot.student = student_enrollment.student
    snapshot.student_enrollment = student_enrollment
    snapshot.academic_year = year
    snapshot.academic_year_level = level
    snapshot.exam = exam
    snapshot.promotion_rule = rule
    if persist:
        db.session.add(snapshot)
        db.session.flush()
    return snapshot


def resolve_evaluation_context(academic_year_id, academic_year_level_id, exam_id):
    """Validate the explicit Year + Level + Exam evaluation context.

    The evaluator never guesses a latest/final exam.  The optional legacy
    ``Exam.academic_level_id`` bridge is checked when present; a null bridge is
    accepted because older exams do not carry that field.
    """
    year, level = validate_rule_scope(academic_year_id, academic_year_level_id)
    try:
        exam_id = int(exam_id)
    except (TypeError, ValueError):
        raise PromotionValidationError("An explicit evaluation exam is required")
    exam = db.session.get(Exam, exam_id)
    if not exam:
        raise PromotionValidationError("The selected evaluation exam was not found")
    if exam.academic_year_id != year.id:
        raise PromotionValidationError("The selected exam does not belong to the selected Academic Year")
    if exam.academic_level_id is not None:
        if level.legacy_level_id is None or level.legacy_level_id != exam.academic_level_id:
            raise PromotionValidationError("The selected exam does not belong to the selected Academic Year Level")
    return year, level, exam


def evaluation_subjects(academic_year_id, academic_year_level_id, subject_ids=None):
    """Resolve the exact subject set for one evaluation, never globally."""
    valid = valid_critical_subjects(academic_year_id, academic_year_level_id)
    valid_by_id = {item.id: item for item in valid}
    if subject_ids is None:
        if not valid:
            raise PromotionValidationError("No active subjects are configured for the selected Academic Year Level")
        return valid
    if subject_ids in ((), [], ""):
        raise PromotionValidationError("Select at least one subject for the evaluation")
    normalized = []
    for raw_id in subject_ids:
        try:
            subject_id = int(raw_id)
        except (TypeError, ValueError):
            raise PromotionValidationError("Evaluation subject selection is invalid")
        if subject_id not in valid_by_id:
            raise PromotionValidationError("Evaluation subject does not belong to the selected Academic Year Level")
        if subject_id in normalized:
            raise PromotionValidationError("Duplicate evaluation subject selection is not allowed")
        normalized.append(subject_id)
    if not normalized:
        raise PromotionValidationError("Select at least one subject for the evaluation")
    return [valid_by_id[item] for item in normalized]


def _subject_percentage(score, maximum):
    try:
        score = Decimal(str(score))
        maximum = Decimal(str(maximum))
    except (InvalidOperation, TypeError, ValueError):
        raise PromotionValidationError("Result score or maximum is invalid")
    if maximum <= 0 or score < 0 or score > maximum:
        raise PromotionValidationError("Result score is outside the configured subject range")
    return score, maximum, (score / maximum * Decimal("100")).quantize(Decimal("0.001"))


def _result_snapshot_for_student(student_enrollment, exam, subjects):
    """Load only this student's published results and exact year-level subjects."""
    subject_ids = {subject.id for subject in subjects}
    subjects_by_legacy = {}
    for subject in subjects:
        if subject.legacy_subject_id is not None:
            subjects_by_legacy.setdefault(subject.legacy_subject_id, []).append(subject)

    rows = (
        Result.query
        .filter_by(student_id=student_enrollment.student_id, exam_id=exam.id, is_published=True)
        .order_by(Result.id)
        .all()
    )
    mapped = {}
    issues = []
    for row in rows:
        candidates = subjects_by_legacy.get(row.subject_id, [])
        if len(candidates) != 1:
            issues.append(f"Result subject {row.subject_id} has no unique mapping in the selected Academic Year Level")
            continue
        subject = candidates[0]
        if subject.id not in subject_ids:
            # The explicit evaluation subject set intentionally excludes this
            # result; it must not affect the selected evaluation.
            continue
        if subject.id in mapped:
            issues.append(f"Duplicate result for subject {subject.name}")
            continue
        try:
            score, maximum, percentage = _subject_percentage(row.score, subject.max_score)
        except PromotionValidationError as exc:
            issues.append(f"{subject.name}: {exc}")
            continue
        mapped[subject.id] = {
            "academic_year_subject_id": subject.id,
            "subject": subject.name,
            "score": score,
            "maximum": maximum,
            "percentage": percentage,
            "status": "VALID",
        }

    missing = [subject for subject in subjects if subject.id not in mapped]
    if issues:
        status = "INVALID"
        reason = "; ".join(issues)
    elif not mapped:
        status = "INCOMPLETE"
        reason = "NO_VALID_RESULTS"
    elif missing:
        status = "INCOMPLETE"
        reason = "MISSING_RESULTS: " + ", ".join(subject.name for subject in missing)
    else:
        status = "EVALUATED"
        reason = None
    return mapped, status, reason


def evaluate_student_promotion(student_enrollment, evaluation_context, *, persist=True):
    """Evaluate one enrollment from the selected, explicit exam context."""
    if not isinstance(student_enrollment, StudentEnrollment):
        raise PromotionValidationError("A StudentEnrollment is required")
    if not isinstance(evaluation_context, dict):
        raise PromotionValidationError("Evaluation context must be an object")
    year, level, exam = resolve_evaluation_context(
        evaluation_context.get("academic_year_id", student_enrollment.academic_year_id),
        evaluation_context.get("academic_year_level_id", student_enrollment.academic_year_level_id),
        evaluation_context.get("exam_id"),
    )
    if student_enrollment.academic_year_id != year.id or student_enrollment.academic_year_level_id != level.id:
        raise PromotionValidationError("StudentEnrollment does not belong to the selected evaluation scope")
    subjects = evaluation_subjects(year.id, level.id, evaluation_context.get("subject_ids"))
    mapped, evaluation_status, data_reason = _result_snapshot_for_student(
        student_enrollment, exam, subjects
    )
    overall_percentage = None
    base_outcome = None
    final_outcome = None
    override_reason = data_reason
    enabled = promotion_rules_enabled()
    rule = get_promotion_rule(year.id, level.id, active_only=True) if enabled else None
    overall_threshold = Decimal(str(rule.overall_pass_threshold)) if rule else DEFAULT_PROMOTION_THRESHOLD
    critical_threshold = Decimal(str(rule.critical_subject_pass_threshold)) if rule else DEFAULT_PROMOTION_THRESHOLD
    if evaluation_status == "EVALUATED":
        total_score = sum((row["score"] for row in mapped.values()), Decimal("0"))
        total_max = sum((row["maximum"] for row in mapped.values()), Decimal("0"))
        overall_percentage = (total_score / total_max * Decimal("100")).quantize(Decimal("0.001")) if total_max else None
        if overall_percentage is None:
            evaluation_status = "INVALID"
            override_reason = "NO_VALID_MAXIMUM"
        else:
            base_outcome = "PASS" if overall_percentage >= overall_threshold else "FAIL"
            final_outcome = base_outcome

    critical_results = []
    failed_critical_subjects = []
    if rule:
        for mapping in rule.critical_subjects:
            subject = mapping.academic_year_subject
            result = mapped.get(subject.id)
            if evaluation_status != "EVALUATED" or not result:
                status = "NOT_EVALUATED"
                percentage = None
            else:
                percentage = result["percentage"]
                status = "PASS" if percentage >= critical_threshold else "FAIL"
                if status == "FAIL":
                    failed_critical_subjects.append(subject.id)
            critical_results.append({
                "academic_year_subject_id": subject.id,
                "subject": subject.name,
                "score": result["score"] if result else None,
                "maximum": result["maximum"] if result else subject.max_score,
                "percentage": percentage,
                "threshold": critical_threshold,
                "status": status,
            })
    if evaluation_status == "EVALUATED" and failed_critical_subjects:
        final_outcome = "FAIL"
        override_reason = "FAILED_CRITICAL_SUBJECT"

    context_snapshot = dict(evaluation_context)
    context_snapshot.update({
        "academic_year_id": year.id,
        "academic_year_name": year.name,
        "academic_year_level_id": level.id,
        "academic_year_level_name": level.name,
        "exam_id": exam.id,
        "exam_name": exam.name,
        "subject_ids": [subject.id for subject in subjects],
        "evaluation_status": evaluation_status,
        "reason": override_reason,
        "results": list(mapped.values()),
        "evaluated_at": datetime.utcnow(),
    })
    snapshot = PromotionEvaluation(
        student_id=student_enrollment.student_id,
        student_enrollment_id=student_enrollment.id,
        academic_year_id=year.id,
        academic_year_level_id=level.id,
        exam_id=exam.id,
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
        evaluation_status=evaluation_status,
        critical_subject_results_json=json.dumps(critical_results, default=_json_default, sort_keys=True),
        override_reason=override_reason,
        evaluated_at=datetime.utcnow(),
    )
    if persist:
        db.session.add(snapshot)
        db.session.flush()
    return snapshot


def evaluate_promotion_scope(
    academic_year_id,
    academic_year_level_id,
    exam_id,
    *,
    academic_year_class_id=None,
    subject_ids=None,
    persist=False,
):
    """Preview or execute every eligible enrollment in one isolated scope."""
    year, level, exam = resolve_evaluation_context(
        academic_year_id, academic_year_level_id, exam_id
    )
    if academic_year_class_id not in (None, ""):
        try:
            class_id = int(academic_year_class_id)
        except (TypeError, ValueError):
            raise PromotionValidationError("Academic Year Class selection is invalid")
        year_class = db.session.get(AcademicYearClass, class_id)
        if not year_class or year_class.academic_year_level_id != level.id:
            raise PromotionValidationError("Academic Year Class does not belong to the selected Academic Year Level")
        academic_year_class_id = class_id
    subjects = evaluation_subjects(year.id, level.id, subject_ids)
    query = StudentEnrollment.query.filter(
        StudentEnrollment.academic_year_id == year.id,
        StudentEnrollment.academic_year_level_id == level.id,
        StudentEnrollment.status.in_(("active", "completed")),
    )
    if academic_year_class_id not in (None, ""):
        query = query.filter(StudentEnrollment.academic_year_class_id == academic_year_class_id)
    enrollments = query.order_by(StudentEnrollment.id).all()
    context = {
        "academic_year_id": year.id,
        "academic_year_level_id": level.id,
        "academic_year_class_id": academic_year_class_id,
        "exam_id": exam.id,
        "subject_ids": [subject.id for subject in subjects],
    }
    snapshots = []
    preview_rows = []
    for enrollment in enrollments:
        snapshot = evaluate_student_promotion(enrollment, context, persist=persist)
        snapshots.append(snapshot)
        preview_rows.append({
            "evaluation": snapshot,
            "student": enrollment.student,
            "enrollment": enrollment,
        })
    counts = {
        "eligible": len(snapshots),
        "evaluated": sum(item.evaluation_status == "EVALUATED" for item in snapshots),
        "incomplete": sum(item.evaluation_status == "INCOMPLETE" for item in snapshots),
        "invalid": sum(item.evaluation_status == "INVALID" for item in snapshots),
        "pass": sum(item.final_outcome == "PASS" for item in snapshots),
        "fail": sum(item.final_outcome == "FAIL" for item in snapshots),
        "skipped": 0,
    }
    return {
        "year": year,
        "level": level,
        "exam": exam,
        "subjects": subjects,
        "enrollments": enrollments,
        "snapshots": snapshots,
        "preview_rows": preview_rows,
        "counts": counts,
    }
