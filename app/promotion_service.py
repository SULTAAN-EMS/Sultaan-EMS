"""Promotion-rule configuration, evaluation, and outcome workflow helpers.

Evaluation snapshots are immutable, exam-specific evidence. Saving a session
evaluation never changes the student's academic placement; only an explicit
Final Evaluation may be applied to the enrollment and then used for a
promotion, repeat, or graduation transition.
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
    PromotionOutcomeApplication,
    PromotionRule,
    PromotionRuleCriticalSubject,
    Result,
    Setting,
    StudentEnrollmentMovement,
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


def validate_exam_scope(academic_year_id, academic_year_level_id, exam_id):
    """Resolve an explicit Results exam inside the exact year-level scope."""
    year, level = validate_rule_scope(academic_year_id, academic_year_level_id)
    try:
        exam_id = int(exam_id)
    except (TypeError, ValueError):
        raise PromotionValidationError("An explicit Exam Type is required")
    exam = db.session.get(Exam, exam_id)
    if not exam or exam.academic_year_id != year.id:
        raise PromotionValidationError("The selected Exam Type does not belong to the selected Academic Year")
    if exam.academic_level_id is not None:
        if level.legacy_level_id is None or level.legacy_level_id != exam.academic_level_id:
            raise PromotionValidationError("The selected Exam Type does not belong to the selected Academic Year Level")
    return year, level, exam


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


def get_promotion_rule(academic_year_id, academic_year_level_id, *, exam_id=None, active_only=False):
    """Load a rule from its exact year + level + exam scope.

    ``exam_id=None`` intentionally addresses only legacy Phase 3B rows. New
    explicit-exam evaluations never fall back to those rows.
    """
    validate_rule_scope(academic_year_id, academic_year_level_id)
    query = PromotionRule.query.filter_by(
        academic_year_id=academic_year_id,
        academic_year_level_id=academic_year_level_id,
    )
    if exam_id in (None, ""):
        query = query.filter(PromotionRule.exam_id.is_(None))
    else:
        _, _, exam = validate_exam_scope(academic_year_id, academic_year_level_id, exam_id)
        query = query.filter_by(exam_id=exam.id)
    if active_only:
        query = query.filter_by(is_active=True)
    rule = query.first()
    if rule and rule.academic_year_level.academic_year_id != rule.academic_year_id:
        raise PromotionValidationError("Promotion Rule scope is internally inconsistent")
    return rule


def promotion_rules_active_for_enrollment(enrollment, *, exam_id=None):
    """Return whether the exact enrollment scope is controlled by Promotion Rules."""
    if not isinstance(enrollment, StudentEnrollment) or not promotion_rules_enabled():
        return False
    return get_promotion_rule(
        enrollment.academic_year_id,
        enrollment.academic_year_level_id,
        exam_id=exam_id,
        active_only=True,
    ) is not None


def upsert_promotion_rule(
    academic_year_id,
    academic_year_level_id,
    *,
    exam_id=None,
    is_active=True,
    overall_pass_threshold=DEFAULT_PROMOTION_THRESHOLD,
    critical_subject_pass_threshold=DEFAULT_PROMOTION_THRESHOLD,
    critical_subject_ids=None,
):
    """Create/update one rule while validating every year-aware relationship.

    ``exam_id`` remains optional for the legacy service API, but the admin UI
    always supplies it so new rules cannot be shared across exam types.
    """
    if exam_id in (None, ""):
        validate_rule_scope(academic_year_id, academic_year_level_id)
        resolved_exam = None
    else:
        _, _, resolved_exam = validate_exam_scope(
            academic_year_id, academic_year_level_id, exam_id
        )
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
    rule = get_promotion_rule(
        academic_year_id,
        academic_year_level_id,
        exam_id=resolved_exam.id if resolved_exam else None,
    )
    if rule is None:
        rule = PromotionRule(
            academic_year_id=academic_year_id,
            academic_year_level_id=academic_year_level_id,
            exam_id=resolved_exam.id if resolved_exam else None,
        )
        db.session.add(rule)
        db.session.flush()
    rule.is_active = bool(is_active)
    rule.overall_pass_threshold = overall_threshold
    rule.critical_subject_pass_threshold = critical_threshold
    # Flush removals before inserting the replacement mappings.  Without this
    # ordering, saving the same rule twice can hit the unique constraint while
    # SQLAlchemy still has the old subject mappings in the database.
    rule.critical_subjects.clear()
    db.session.flush()
    rule.critical_subjects.extend(
        PromotionRuleCriticalSubject(academic_year_subject_id=subject.id)
        for subject in subjects
    )
    db.session.flush()
    return rule


def promotion_rule_snapshot(rule, *, enabled):
    """Serialize the mutable rule into a historical-safe JSON payload."""
    if rule is None:
        return {
            "feature_enabled": bool(enabled),
            "rule_id": None,
            "exam_id": None,
            "overall_pass_threshold": float(DEFAULT_PROMOTION_THRESHOLD),
            "critical_subject_pass_threshold": float(DEFAULT_PROMOTION_THRESHOLD),
            "critical_subjects": [],
        }
    return {
        "feature_enabled": bool(enabled),
        "rule_id": rule.id,
        "academic_year_id": rule.academic_year_id,
        "academic_year_level_id": rule.academic_year_level_id,
        "exam_id": rule.exam_id,
        "exam_name": rule.exam.name if rule.exam else None,
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
    rule = get_promotion_rule(
        year.id,
        level.id,
        exam_id=exam.id if exam else None,
        active_only=True,
    ) if enabled else None
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
    return validate_exam_scope(
        academic_year_id,
        academic_year_level_id,
        exam_id,
    )


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
    # Explicit evaluations must use only the rule for this exact exam. A
    # legacy year-level rule is preserved for history but never leaks here.
    rule = get_promotion_rule(
        year.id,
        level.id,
        exam_id=exam.id,
        active_only=True,
    ) if enabled else None
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
    # Kept for response/template compatibility. Phase 4B deliberately leaves
    # StudentEnrollment untouched until an explicit Final Evaluation apply.
    outcomes_saved = 0
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
        "outcomes_saved": outcomes_saved,
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


def latest_promotion_evaluation(enrollment, *, exam_id=None):
    """Return the latest snapshot for one exact enrollment scope.

    The enrollment is the anchor for the lookup.  This prevents a current
    student placement from accidentally picking up a snapshot from another
    academic year, level, or historical enrollment.
    """
    if not isinstance(enrollment, StudentEnrollment):
        return None
    query = PromotionEvaluation.query.filter_by(
        student_id=enrollment.student_id,
        student_enrollment_id=enrollment.id,
        academic_year_id=enrollment.academic_year_id,
        academic_year_level_id=enrollment.academic_year_level_id,
    )
    if exam_id not in (None, ""):
        query = query.filter_by(exam_id=int(exam_id))
    return query.order_by(
        PromotionEvaluation.evaluated_at.desc(),
        PromotionEvaluation.id.desc(),
    ).first()


def promotion_operational_status(enrollment, *, exam_id=None):
    """Derive one administrator-facing status from authoritative records."""
    evaluation = latest_promotion_evaluation(enrollment, exam_id=exam_id)
    application = _application_for(evaluation.id) if evaluation else None
    result = {
        "code": "NOT_EVALUATED",
        "eligibility_code": None,
        "label": "Not evaluated",
        "evaluation_outcome": None,
        "tone": "muted",
        "reason": "No exact evaluation snapshot exists for this enrollment.",
        "evaluation": evaluation,
        "application": application,
        "eligible_actions": [],
        "rules_active": promotion_rules_active_for_enrollment(
            enrollment,
            exam_id=exam_id,
        ),
    }
    if evaluation is None:
        return result

    if evaluation.evaluation_status != "EVALUATED" or evaluation.final_outcome not in PromotionEvaluation.OUTCOME_VALUES:
        result.update({
            "code": "BLOCKED",
            "label": evaluation.evaluation_status.replace("_", " ").title(),
            "tone": "danger" if evaluation.evaluation_status == "INVALID" else "warning",
            "reason": "The evaluation is incomplete or invalid and cannot authorize a transition.",
        })
        return result

    result["evaluation_outcome"] = "GUDBAY" if evaluation.final_outcome == "PASS" else "HADHAY"

    # Non-final exams produce a real immutable PASS/FAIL academic result, but
    # they are never authorization evidence for promotion, repeat, or
    # graduation.
    if evaluation.exam and not evaluation.exam.is_final_evaluation:
        if application and application.action != "outcome":
            result.update({
                "code": "BLOCKED",
                "label": "Invalid non-final transition",
                "tone": "danger",
                "reason": "A non-final evaluation cannot authorize a transition.",
            })
        elif application:
            result.update({
                "code": "NON_FINAL_OUTCOME_APPLIED",
                "label": "Non-final outcome applied",
                "tone": "info",
                "reason": "This exam records PASS/FAIL only; no promotion, repeat, or graduation is available.",
            })
        else:
            result.update({
                "code": "NON_FINAL_EVALUATED",
                "label": "Non-final evaluation saved",
                "tone": "info",
                "reason": "This exam records PASS/FAIL only; no promotion, repeat, or graduation is available.",
            })
        return result

    if application and application.application_status in {"TRANSITIONED", "GRADUATED"}:
        result.update({
            "code": "TRANSITION_COMPLETED",
            "label": application.action.title() + " completed",
            "tone": "success",
            "reason": "The approved outcome has already been completed.",
        })
        return result

    if application and application.application_status == "APPLIED":
        if application.applied_outcome == "passed":
            eligible_actions = [
                "graduation" if is_final_academic_year_level(enrollment.academic_year_level_id) else "promotion"
            ]
        else:
            eligible_actions = ["repeat"]
        result.update({
            "code": "OUTCOME_APPLIED",
            "eligibility_code": "ELIGIBLE_FOR_TRANSITION",
            "label": "Outcome applied",
            "tone": "success",
            "reason": "The PASS/FAIL outcome is applied and ready for the next action.",
            "eligible_actions": eligible_actions,
        })
        return result

    expected_outcome = "passed" if evaluation.final_outcome == "PASS" else "failed"
    if enrollment.academic_outcome == expected_outcome:
        eligible_actions = (
            ["graduation" if is_final_academic_year_level(enrollment.academic_year_level_id) else "promotion"]
            if expected_outcome == "passed"
            else ["repeat"]
        )
        result.update({
            "code": "OUTCOME_APPLIED",
            "eligibility_code": "ELIGIBLE_FOR_TRANSITION",
            "label": "Outcome saved",
            "tone": "success",
            "reason": "The evaluated PASS/FAIL outcome is saved and ready for the next action.",
            "eligible_actions": eligible_actions,
        })
    else:
        result.update({
            "code": "EVALUATED_NOT_APPLIED",
            "label": "Evaluated — not applied",
            "tone": "info",
            "reason": "A complete evaluation exists, but its academic outcome is not yet applied.",
        })
    return result


def portal_academic_outcome(enrollment, *, exam_id=None):
    """Resolve the student-facing outcome from exact evaluation evidence.

    ``StudentEnrollment.academic_outcome`` is a transition ledger value, not
    evidence that an exam was evaluated.  The portal therefore starts from a
    neutral state and only exposes GUDBAY/HADHAY after an exact, complete
    evaluation exists.  PROMOTED/REPEATED/GRADUATED are exposed only after the
    linked outcome application records a completed transition.
    """
    result = {"code": "NOT_EVALUATED", "label": "LAMA QIIMEYN", "tone": "muted"}
    if not isinstance(enrollment, StudentEnrollment) or exam_id in (None, ""):
        return result

    evaluation = latest_promotion_evaluation(enrollment, exam_id=exam_id)
    if (
        not evaluation
        or evaluation.evaluation_status != "EVALUATED"
        or evaluation.final_outcome not in PromotionEvaluation.OUTCOME_VALUES
        or not evaluation.exam
        or evaluation.exam.academic_year_id != enrollment.academic_year_id
    ):
        return result

    # A session result is student-visible history only. It must not inherit a
    # promotion/repeat/graduation status from the enrollment or an old ledger.
    if not evaluation.exam.is_final_evaluation:
        if evaluation.final_outcome == "PASS":
            return {"code": "PASSED", "label": "GUDBAY", "tone": "success"}
        return {"code": "FAILED", "label": "HADHAY", "tone": "danger"}

    application = _application_for(evaluation.id)
    if application and (
        application.student_id != enrollment.student_id
        or application.source_enrollment_id != enrollment.id
    ):
        application = None
    if application and application.application_status == "TRANSITIONED":
        if application.action == "promotion":
            return {"code": "PROMOTED", "label": "U GUDBAY FASALKA XIGA", "tone": "success"}
        if application.action == "repeat":
            return {"code": "REPEATED", "label": "KU CELINAYA FASALKA", "tone": "warning"}
    if application and application.application_status == "GRADUATED":
        return {"code": "GRADUATED", "label": "QALINJABIYEY", "tone": "success"}

    if evaluation.final_outcome == "PASS":
        return {"code": "PASSED", "label": "GUDBAY", "tone": "success"}
    return {"code": "FAILED", "label": "HADHAY", "tone": "danger"}


def promotion_scope_summary(academic_year_id, academic_year_level_id, *, academic_year_class_id=None, exam_id=None):
    """Build a strictly year + level (+ class) scoped operational summary."""
    year, level = validate_rule_scope(academic_year_id, academic_year_level_id)
    year_class = None
    if academic_year_class_id not in (None, ""):
        year_class = db.session.get(AcademicYearClass, int(academic_year_class_id))
        if not year_class or year_class.academic_year_level_id != level.id:
            raise PromotionValidationError("Academic Year Class does not belong to the selected Academic Year Level")
    selected_exam = None
    if exam_id not in (None, ""):
        selected_exam = db.session.get(Exam, int(exam_id))
        if not selected_exam or selected_exam.academic_year_id != year.id:
            raise PromotionValidationError("The selected exam does not belong to the selected Academic Year")

    query = StudentEnrollment.query.filter(
        StudentEnrollment.academic_year_id == year.id,
        StudentEnrollment.academic_year_level_id == level.id,
        StudentEnrollment.status.in_(("active", "completed")),
    )
    if year_class:
        query = query.filter(StudentEnrollment.academic_year_class_id == year_class.id)
    enrollments = query.order_by(StudentEnrollment.id).all()
    counts = {
        "total_students": len(enrollments),
        "evaluated": 0,
        "not_evaluated": 0,
        "passed": 0,
        "failed": 0,
        "incomplete": 0,
        "invalid": 0,
        "outcome_applied": 0,
        "eligible_promotion": 0,
        "eligible_repeat": 0,
        "eligible_graduation": 0,
        "transition_completed": 0,
    }
    rows = []
    for enrollment in enrollments:
        status = promotion_operational_status(enrollment, exam_id=exam_id)
        evaluation = status["evaluation"]
        application = status["application"]
        if evaluation is None:
            counts["not_evaluated"] += 1
        elif evaluation.evaluation_status == "EVALUATED" and evaluation.final_outcome in PromotionEvaluation.OUTCOME_VALUES:
            counts["evaluated"] += 1
            counts["passed" if evaluation.final_outcome == "PASS" else "failed"] += 1
        elif evaluation.evaluation_status == "INCOMPLETE":
            counts["incomplete"] += 1
        elif evaluation.evaluation_status == "INVALID":
            counts["invalid"] += 1
        if application:
            if application.application_status == "APPLIED":
                counts["outcome_applied"] += 1
            elif application.application_status in {"TRANSITIONED", "GRADUATED"}:
                counts["transition_completed"] += 1
        for action in status["eligible_actions"]:
            counts[f"eligible_{action}"] += 1
        rows.append({"student": enrollment.student, "enrollment": enrollment, "status": status})
    return {
        "year": year,
        "level": level,
        "academic_class": year_class,
        "exam": selected_exam,
        "workflow": (
            "FINAL" if selected_exam and selected_exam.is_final_evaluation
            else "SESSION" if selected_exam
            else "ALL"
        ),
        "counts": counts,
        "rows": rows,
    }


def promotion_consistency_audit(*, academic_year_id=None, academic_year_level_id=None, limit=1000):
    """Read-only audit for broken promotion/evaluation/movement linkage."""
    query = PromotionEvaluation.query
    if academic_year_id:
        query = query.filter_by(academic_year_id=int(academic_year_id))
    if academic_year_level_id:
        query = query.filter_by(academic_year_level_id=int(academic_year_level_id))
    evaluations = query.order_by(PromotionEvaluation.id).limit(limit).all()
    evaluation_ids = {evaluation.id for evaluation in evaluations}
    applications_query = PromotionOutcomeApplication.query.order_by(PromotionOutcomeApplication.id)
    if academic_year_id or academic_year_level_id:
        applications_query = applications_query.filter(
            PromotionOutcomeApplication.promotion_evaluation_id.in_(evaluation_ids or {-1})
        )
    applications = applications_query.all()
    movements_query = StudentEnrollmentMovement.query.order_by(StudentEnrollmentMovement.id)
    if academic_year_id:
        movements_query = movements_query.filter(
            StudentEnrollmentMovement.from_academic_year_id == int(academic_year_id)
        )
    elif academic_year_level_id:
        movements_query = movements_query.filter(
            StudentEnrollmentMovement.from_academic_year_level_id == int(academic_year_level_id)
        )
    movements = movements_query.all()
    anomalies = []

    def add(code, message, *, evaluation=None, application=None, movement=None):
        anomalies.append({
            "code": code,
            "message": message,
            "evaluation_id": evaluation.id if evaluation else None,
            "application_id": application.id if application else None,
            "movement_id": movement.id if movement else None,
        })

    for evaluation in evaluations:
        year_level = db.session.get(AcademicYearLevel, evaluation.academic_year_level_id)
        if not year_level or year_level.academic_year_id != evaluation.academic_year_id:
            add("EVALUATION_YEAR_LEVEL_MISMATCH", "Evaluation level does not belong to its Academic Year.", evaluation=evaluation)
        source = db.session.get(StudentEnrollment, evaluation.student_enrollment_id)
        if not source or source.student_id != evaluation.student_id:
            add("EVALUATION_ENROLLMENT_MISMATCH", "Evaluation does not match its source StudentEnrollment.", evaluation=evaluation)
        elif source.academic_year_id != evaluation.academic_year_id or source.academic_year_level_id != evaluation.academic_year_level_id:
            add("EVALUATION_SCOPE_MISMATCH", "Evaluation and source enrollment have different year/level scope.", evaluation=evaluation)
        if evaluation.exam_id:
            exam = db.session.get(Exam, evaluation.exam_id)
            if not exam or exam.academic_year_id != evaluation.academic_year_id:
                add("EVALUATION_EXAM_YEAR_MISMATCH", "Evaluation exam does not belong to its Academic Year.", evaluation=evaluation)

    apps_by_eval = {}
    for application in applications:
        apps_by_eval.setdefault(application.promotion_evaluation_id, []).append(application)
        evaluation = db.session.get(PromotionEvaluation, application.promotion_evaluation_id)
        if not evaluation:
            add("APPLICATION_INVALID_EVALUATION", "Outcome application references a missing evaluation.", application=application)
            continue
        if application.student_id != evaluation.student_id or application.source_enrollment_id != evaluation.student_enrollment_id:
            add("APPLICATION_SOURCE_MISMATCH", "Outcome application does not match evaluation student/enrollment.", evaluation=evaluation, application=application)
        if application.applied_outcome == "passed" and application.action == "repeat":
            add("PASS_LINKED_TO_REPEAT", "PASS evaluation is linked to Repeat.", evaluation=evaluation, application=application)
        if application.applied_outcome == "failed" and application.action == "promotion":
            add("FAIL_LINKED_TO_PROMOTION", "FAIL evaluation is linked to Promotion.", evaluation=evaluation, application=application)
        source = db.session.get(StudentEnrollment, application.source_enrollment_id)
        if application.action == "graduation" and source and not is_final_academic_year_level(source.academic_year_level_id):
            add("GRADUATION_NON_FINAL_LEVEL", "Graduation is linked to a non-final Academic Year Level.", evaluation=evaluation, application=application)
        if application.action in {"promotion", "repeat"} and application.application_status == "TRANSITIONED":
            if not application.destination_enrollment_id or not application.movement_id:
                add("BROKEN_TRANSITION_LINK", "Completed transition is missing destination or movement linkage.", evaluation=evaluation, application=application)
        if application.application_status == "GRADUATED" and application.destination_enrollment_id:
            add("GRADUATION_HAS_DESTINATION", "Graduation must not have a destination enrollment.", evaluation=evaluation, application=application)
        if application.movement_id:
            movement = db.session.get(StudentEnrollmentMovement, application.movement_id)
            expected = application.action
            if not movement or movement.student_id != application.student_id or movement.movement_type != expected:
                add("BROKEN_MOVEMENT_LINK", "Outcome application movement link is missing or inconsistent.", evaluation=evaluation, application=application, movement=movement)

    for evaluation_id, linked in apps_by_eval.items():
        if len(linked) > 1:
            for application in linked[1:]:
                add("DUPLICATE_OUTCOME_APPLICATION", "More than one outcome application exists for one evaluation.", application=application)

    enrollment_query = StudentEnrollment.query
    if academic_year_id:
        enrollment_query = enrollment_query.filter_by(academic_year_id=int(academic_year_id))
    if academic_year_level_id:
        enrollment_query = enrollment_query.filter_by(academic_year_level_id=int(academic_year_level_id))
    destination_groups = {}
    for enrollment in enrollment_query.order_by(StudentEnrollment.id).all():
        destination_groups.setdefault((enrollment.student_id, enrollment.academic_year_id), []).append(enrollment)
    for key, grouped in destination_groups.items():
        if len(grouped) > 1:
            add("DUPLICATE_DESTINATION_ENROLLMENT", f"Student {key[0]} has duplicate enrollment rows in Academic Year {key[1]}.")

    linked_movement_ids = {application.movement_id for application in applications if application.movement_id}
    for movement in movements:
        if movement.movement_type in {"promotion", "repeat"} and movement.id not in linked_movement_ids:
            add("TRANSITION_WITHOUT_APPLICATION", "Promotion/Repeat movement has no outcome application.", movement=movement)
        destination = movement.enrollment
        if destination is None:
            add("BROKEN_MOVEMENT_LINK", "Movement references a missing destination enrollment.", movement=movement)
            continue
        if (
            movement.to_academic_year_id != destination.academic_year_id
            or movement.to_academic_year_level_id != destination.academic_year_level_id
            or movement.to_academic_year_class_id != destination.academic_year_class_id
        ):
            add("MOVEMENT_DESTINATION_MISMATCH", "Movement destination does not match its enrollment.", movement=movement)
        if movement.movement_type in {"promotion", "repeat"} and movement.from_academic_year_id == movement.to_academic_year_id:
            add("CROSS_YEAR_LEAKAGE", "Promotion/Repeat movement stayed inside the same Academic Year.", movement=movement)

    return {
        "anomalies": anomalies,
        "counts": {"evaluations": len(evaluations), "applications": len(applications), "movements": len(movements), "anomalies": len(anomalies)},
    }


# ---------------------------------------------------------------------------
# Phase 3D: explicit outcome application and transition integration
# ---------------------------------------------------------------------------

def _authorizable_evaluation(evaluation_id):
    """Load one exact, explicit-exam evaluation that may be used once."""
    evaluation = db.session.get(PromotionEvaluation, evaluation_id)
    if not evaluation:
        raise PromotionValidationError("Promotion evaluation was not found")
    if evaluation.exam_id is None:
        raise PromotionValidationError("This legacy evaluation has no explicit exam and cannot authorize a transition")
    if evaluation.evaluation_status != "EVALUATED" or evaluation.final_outcome not in PromotionEvaluation.OUTCOME_VALUES:
        raise PromotionValidationError("Only a complete PASS/FAIL evaluation can authorize an academic outcome")
    exam = db.session.get(Exam, evaluation.exam_id)
    if not exam or exam.academic_year_id != evaluation.academic_year_id:
        raise PromotionValidationError("Evaluation exam does not belong to the evaluated Academic Year")
    if not exam.is_final_evaluation:
        raise PromotionValidationError(
            "Only a Final Evaluation can authorize Apply Outcome; this session stores GUDBAY/HADHAY history only"
        )
    source = db.session.get(StudentEnrollment, evaluation.student_enrollment_id)
    if (
        not source
        or source.student_id != evaluation.student_id
        or source.academic_year_id != evaluation.academic_year_id
        or source.academic_year_level_id != evaluation.academic_year_level_id
    ):
        raise PromotionValidationError("Evaluation source enrollment is outside its immutable Year + Level scope")
    if source.status not in ("active", "completed"):
        raise PromotionValidationError("The source enrollment is no longer eligible for an outcome")
    try:
        from .enrollment_service import validate_enrollment_scope
        validate_enrollment_scope(
            source.academic_year_id,
            source.academic_year_level_id,
            source.academic_year_class_id,
            source.academic_section_id,
        )
    except Exception as exc:
        if isinstance(exc, PromotionValidationError):
            raise
        raise PromotionValidationError("Evaluation source enrollment has an invalid academic scope") from exc
    return evaluation, source


def _application_for(evaluation_id):
    return PromotionOutcomeApplication.query.filter_by(
        promotion_evaluation_id=evaluation_id,
    ).first()


def _ensure_outcome_application(evaluation, source, *, applied_by=None, notes=None):
    """Create the outcome ledger for a saved exact evaluation, idempotently."""
    existing = _application_for(evaluation.id)
    if existing:
        expected = "passed" if evaluation.final_outcome == "PASS" else "failed"
        if existing.source_enrollment_id != source.id or existing.applied_outcome != expected:
            raise PromotionValidationError("Outcome application does not match the immutable evaluation")
        return existing
    applied_outcome = "passed" if evaluation.final_outcome == "PASS" else "failed"
    if source.academic_outcome == "pending":
        source.academic_outcome = applied_outcome
    elif source.academic_outcome != applied_outcome:
        raise PromotionValidationError("The source enrollment outcome conflicts with the immutable evaluation")
    application = PromotionOutcomeApplication(
        promotion_evaluation_id=evaluation.id,
        student_id=evaluation.student_id,
        source_enrollment_id=source.id,
        applied_outcome=applied_outcome,
        action="outcome",
        application_status="APPLIED",
        applied_by=applied_by,
        notes=notes,
    )
    db.session.add(application)
    db.session.flush()
    return application


def _create_outcome_application(evaluation, source, *, applied_by=None, notes=None):
    """Preserve the explicit-apply API while allowing a saved outcome to be applied."""
    existing = _application_for(evaluation.id)
    if existing:
        raise PromotionValidationError("This evaluation has already been applied")
    return _ensure_outcome_application(
        evaluation,
        source,
        applied_by=applied_by,
        notes=notes,
    )


def apply_academic_outcome(evaluation_id, *, applied_by=None, notes=None):
    """Explicitly apply PASS/FAIL to the source enrollment, without moving it."""
    evaluation, source = _authorizable_evaluation(evaluation_id)
    return _create_outcome_application(
        evaluation,
        source,
        applied_by=applied_by,
        notes=notes,
    )


def is_final_academic_year_level(academic_year_level_id):
    """Return whether a year-aware level is the final configured level."""
    level = db.session.get(AcademicYearLevel, academic_year_level_id)
    if not level:
        return False
    levels = (
        AcademicYearLevel.query
        .filter_by(academic_year_id=level.academic_year_id, is_active=True)
        .order_by(AcademicYearLevel.sort_order, AcademicYearLevel.name, AcademicYearLevel.id)
        .all()
    )
    return bool(levels and levels[-1].id == level.id)


def transition_applied_outcome(
    evaluation_id,
    *,
    action,
    destination_academic_year_id=None,
    destination_academic_year_level_id=None,
    destination_academic_year_class_id=None,
    destination_academic_section_id=None,
    performed_by=None,
    notes=None,
):
    """Use an applied outcome for promotion, repeat, or final-level graduation."""
    action = str(action or "").strip().lower()
    if action not in {"promotion", "repeat", "graduation"}:
        raise PromotionValidationError("Choose promotion, repeat, or graduation")
    evaluation, source = _authorizable_evaluation(evaluation_id)
    if not evaluation.exam or not evaluation.exam.is_final_evaluation:
        raise PromotionValidationError(
            "This evaluation is not marked as a Final Evaluation and cannot authorize promotion, repeat, or graduation"
        )
    application = _application_for(evaluation.id)
    if not application or application.application_status != "APPLIED":
        raise PromotionValidationError("Apply the academic outcome before executing a transition")
    if application.source_enrollment_id != source.id:
        raise PromotionValidationError("Outcome application source does not match the evaluation")
    expected = "passed" if evaluation.final_outcome == "PASS" else "failed"
    if application.applied_outcome != expected or source.academic_outcome != expected:
        raise PromotionValidationError("Applied outcome no longer matches the immutable evaluation")

    if action == "graduation":
        if evaluation.final_outcome != "PASS" or not is_final_academic_year_level(source.academic_year_level_id):
            raise PromotionValidationError("Graduation requires a PASS evaluation at the final configured level")
        source.status = "completed"
        source.academic_outcome = "graduated"
        application.action = "graduation"
        application.application_status = "GRADUATED"
        application.completed_at = datetime.utcnow()
        db.session.flush()
        return source, None, application

    if action == "promotion" and evaluation.final_outcome != "PASS":
        raise PromotionValidationError("Promotion requires a PASS evaluation")
    if action == "repeat" and evaluation.final_outcome != "FAIL":
        raise PromotionValidationError("Repeat requires a FAIL evaluation")

    from .enrollment_service import transition_student_enrollment

    try:
        source, destination = transition_student_enrollment(
            evaluation.student_id,
            source.id,
            destination_academic_year_id,
            destination_academic_year_level_id,
            destination_academic_year_class_id,
            destination_academic_section_id,
            action=action,
            notes=notes,
            performed_by=performed_by,
            promotion_workflow=True,
        )
    except Exception as exc:
        from .enrollment_service import EnrollmentValidationError
        if isinstance(exc, EnrollmentValidationError):
            raise PromotionValidationError(str(exc)) from exc
        raise
    movement = (
        StudentEnrollmentMovement.query
        .filter_by(enrollment_id=destination.id, movement_type=action)
        .order_by(StudentEnrollmentMovement.id.desc())
        .first()
    )
    application.action = action
    application.application_status = "TRANSITIONED"
    application.destination_enrollment_id = destination.id
    application.movement_id = movement.id if movement else None
    application.completed_at = datetime.utcnow()
    db.session.flush()
    return source, destination, application


def _evaluation_for_enrollment(enrollment, exam_id):
    return (
        PromotionEvaluation.query
        .filter_by(
            student_id=enrollment.student_id,
            student_enrollment_id=enrollment.id,
            academic_year_id=enrollment.academic_year_id,
            academic_year_level_id=enrollment.academic_year_level_id,
            exam_id=exam_id,
            evaluation_status="EVALUATED",
        )
        .filter(PromotionEvaluation.final_outcome.in_(PromotionEvaluation.OUTCOME_VALUES))
        .order_by(PromotionEvaluation.evaluated_at.desc(), PromotionEvaluation.id.desc())
        .first()
    )


def plan_evaluation_transition(
    source_academic_year_id,
    source_academic_year_level_id,
    source_academic_year_class_id,
    exam_id,
    *,
    action,
    destination_academic_year_id=None,
    destination_academic_year_level_id=None,
    destination_academic_year_class_id=None,
    destination_academic_section_id=None,
):
    """Create a read-only whole-class plan from exact evaluation snapshots."""
    from .enrollment_service import validate_enrollment_scope, EnrollmentValidationError

    action = str(action or "").strip().lower()
    if action not in {"promotion", "repeat", "graduation"}:
        raise PromotionValidationError("Choose promotion, repeat, or graduation")
    source_scope = validate_enrollment_scope(
        source_academic_year_id,
        source_academic_year_level_id,
        source_academic_year_class_id,
    )
    year, level, exam = resolve_evaluation_context(
        source_scope["academic_year"].id,
        source_scope["academic_year_level"].id,
        exam_id,
    )
    if not exam.is_final_evaluation:
        raise PromotionValidationError(
            "Only an Exam Type marked as Final Evaluation can authorize promotion, repeat, or graduation"
        )
    destination_scope = None
    if action != "graduation":
        if None in (destination_academic_year_id, destination_academic_year_level_id, destination_academic_year_class_id):
            raise PromotionValidationError("Destination Academic Year, Level, and Class are required")
        destination_scope = validate_enrollment_scope(
            destination_academic_year_id,
            destination_academic_year_level_id,
            destination_academic_year_class_id,
            destination_academic_section_id,
        )
        if destination_scope["academic_year"].id == year.id:
            raise PromotionValidationError("Promotion and repeat require a different Academic Year")
    elif not is_final_academic_year_level(level.id):
        raise PromotionValidationError("Graduation is available only for the final configured Academic Year Level")

    source_enrollments = (
        StudentEnrollment.query
        .filter_by(
            academic_year_id=year.id,
            academic_year_level_id=level.id,
            academic_year_class_id=source_scope["academic_year_class"].id,
        )
        .filter(StudentEnrollment.status.in_(("active", "completed")))
        .order_by(StudentEnrollment.id)
        .all()
    )
    items = []
    expected_outcome = "PASS" if action in {"promotion", "graduation"} else "FAIL"
    for enrollment in source_enrollments:
        evaluation = _evaluation_for_enrollment(enrollment, exam.id)
        application = _application_for(evaluation.id) if evaluation else None
        classification = "READY"
        reason = None
        if not evaluation:
            classification, reason = "NOT_EVALUATED", "No complete exact-exam evaluation snapshot exists"
        elif evaluation.evaluation_status == "INCOMPLETE":
            classification, reason = "INCOMPLETE", "Required result or attendance context is incomplete"
        elif evaluation.evaluation_status == "INVALID":
            classification, reason = "INVALID", "The evaluation snapshot is invalid and cannot authorize a transition"
        elif evaluation.evaluation_status != "EVALUATED" or evaluation.final_outcome not in PromotionEvaluation.OUTCOME_VALUES:
            classification, reason = "INVALID", "The evaluation does not contain a valid PASS/FAIL outcome"
        elif evaluation.final_outcome != expected_outcome:
            classification, reason = "INVALID", f"Action requires a final {expected_outcome} evaluation"
        elif application and application.application_status != "APPLIED":
            classification, reason = "ALREADY_TRANSITIONED", "This evaluation has already been transitioned"
        elif enrollment.academic_outcome != ("passed" if expected_outcome == "PASS" else "failed"):
            classification, reason = "OUTCOME_NOT_APPLIED", "Evaluate & Save the matching academic outcome before executing a transition"
        elif destination_scope and StudentEnrollment.query.filter_by(
            student_id=enrollment.student_id,
            academic_year_id=destination_scope["academic_year"].id,
        ).first():
            classification, reason = "DESTINATION_CONFLICT", "Student already has an enrollment in the destination Academic Year"
        items.append({
            "student": enrollment.student,
            "source": enrollment,
            "evaluation": evaluation,
            "application": application,
            "eligible": classification == "READY",
            "classification": classification,
            "reason": reason,
            "destination": destination_scope,
        })
    return {
        "action": action,
        "year": year,
        "level": level,
        "exam": exam,
        "source_scope": source_scope,
        "destination_scope": destination_scope,
        "items": items,
        "counts": {
            "total": len(items),
            "eligible": sum(item["eligible"] for item in items),
            "ready": sum(item["classification"] == "READY" for item in items),
            "not_evaluated": sum(item["classification"] == "NOT_EVALUATED" for item in items),
            "incomplete": sum(item["classification"] == "INCOMPLETE" for item in items),
            "invalid": sum(item["classification"] == "INVALID" for item in items),
            "outcome_not_applied": sum(item["classification"] == "OUTCOME_NOT_APPLIED" for item in items),
            "already_transitioned": sum(item["classification"] == "ALREADY_TRANSITIONED" for item in items),
            "destination_conflict": sum(item["classification"] == "DESTINATION_CONFLICT" for item in items),
        },
    }


def execute_evaluation_transition_plan(plan, *, performed_by=None, notes=None):
    """Atomically apply each eligible exact evaluation in a reviewed plan."""
    created = []
    eligible_items = [item for item in plan.get("items", []) if item.get("eligible")]
    with db.session.begin_nested():
        for item in eligible_items:
            evaluation = db.session.get(PromotionEvaluation, item["evaluation"].id)
            source = db.session.get(StudentEnrollment, item["source"].id)
            application = _application_for(evaluation.id)
            if application and application.application_status != "APPLIED":
                raise PromotionValidationError("A reviewed evaluation was already transitioned")
            if application is None:
                application = _ensure_outcome_application(
                    evaluation,
                    source,
                    applied_by=performed_by,
                    notes=notes,
                )
            destination_scope = plan.get("destination_scope") or {}
            _, destination, application = transition_applied_outcome(
                evaluation.id,
                action=plan["action"],
                destination_academic_year_id=destination_scope.get("academic_year").id if destination_scope else None,
                destination_academic_year_level_id=destination_scope.get("academic_year_level").id if destination_scope else None,
                destination_academic_year_class_id=destination_scope.get("academic_year_class").id if destination_scope else None,
                destination_academic_section_id=destination_scope.get("academic_section").id if destination_scope and destination_scope.get("academic_section") else None,
                performed_by=performed_by,
                notes=notes,
            )
            created.append({"student": item["student"], "destination": destination, "application": application})
        db.session.flush()
    return created
