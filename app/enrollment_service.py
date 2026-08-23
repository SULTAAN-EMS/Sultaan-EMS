"""Phase 2B StudentEnrollment foundation services.

These helpers intentionally do not change Student's legacy placement fields
or switch any existing route to the new enrollment layer. They provide one
validated API for later phases and a conservative legacy backfill report.
"""

import json
from datetime import datetime
from pathlib import Path

from sqlalchemy import and_, exists, or_

from . import db
from .models import (
    AcademicSection,
    AcademicYear,
    AcademicYearClass,
    AcademicYearLevel,
    AcademicClass,
    SchoolClass,
    Student,
    StudentEnrollment,
)


class EnrollmentValidationError(ValueError):
    """Raised when an enrollment scope does not match the year hierarchy."""


def _require(value, label):
    if value is None or value == "":
        raise EnrollmentValidationError(f"{label} is required")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise EnrollmentValidationError(f"{label} must be an integer") from exc


def validate_enrollment_scope(
    academic_year_id,
    academic_year_level_id,
    academic_year_class_id,
    academic_section_id=None,
):
    """Return validated hierarchy records or raise a precise validation error."""
    year_id = _require(academic_year_id, "Academic year")
    year_level_id = _require(academic_year_level_id, "Academic year level")
    year_class_id = _require(academic_year_class_id, "Academic year class")
    section_id = None if academic_section_id in (None, "") else _require(academic_section_id, "Academic section")

    year = db.session.get(AcademicYear, year_id)
    if not year:
        raise EnrollmentValidationError("Academic year does not exist")

    year_level = db.session.get(AcademicYearLevel, year_level_id)
    if not year_level or year_level.academic_year_id != year_id:
        raise EnrollmentValidationError("Academic year level does not belong to the selected academic year")

    year_class = db.session.get(AcademicYearClass, year_class_id)
    if not year_class or year_class.academic_year_level_id != year_level_id:
        raise EnrollmentValidationError("Academic year class does not belong to the selected academic year level")

    section = None
    if section_id is not None:
        section = db.session.get(AcademicSection, section_id)
        if not section:
            raise EnrollmentValidationError("Academic section does not exist")
        if year_class.legacy_class_id is None or section.academic_class_id != year_class.legacy_class_id:
            raise EnrollmentValidationError("Academic section does not belong to the selected academic year class")

    return {
        "academic_year": year,
        "academic_year_level": year_level,
        "academic_year_class": year_class,
        "academic_section": section,
    }


def get_enrollment_for_student_year(student_id, academic_year_id):
    """Return the single enrollment allowed for a student/year pair."""
    return StudentEnrollment.query.filter_by(
        student_id=_require(student_id, "Student"),
        academic_year_id=_require(academic_year_id, "Academic year"),
    ).first()


def student_enrollment_scope_query(
    academic_year_id,
    academic_year_level_id=None,
    academic_year_class_id=None,
    academic_section_id=None,
):
    """Return an enrollment-first Student query for one year-aware scope.

    Legacy students are included only when they have no enrollment for the
    selected year. This keeps old records visible during the cutover without
    allowing a different year's placement to leak into the selected scope.
    """
    year_id = _require(academic_year_id, "Academic year")
    year = db.session.get(AcademicYear, year_id)
    if not year:
        raise EnrollmentValidationError("Academic year does not exist")

    year_level = None
    if academic_year_level_id not in (None, ""):
        year_level = db.session.get(AcademicYearLevel, _require(academic_year_level_id, "Academic year level"))
        if not year_level or year_level.academic_year_id != year_id:
            raise EnrollmentValidationError("Academic year level does not belong to the selected academic year")

    year_class = None
    if academic_year_class_id not in (None, ""):
        year_class = db.session.get(AcademicYearClass, _require(academic_year_class_id, "Academic year class"))
        if not year_class:
            raise EnrollmentValidationError("Academic year class does not exist")
        if year_level and year_class.academic_year_level_id != year_level.id:
            raise EnrollmentValidationError("Academic year class does not belong to the selected academic year level")
        year_level = year_level or year_class.academic_year_level
        if year_level.academic_year_id != year_id:
            raise EnrollmentValidationError("Academic year class does not belong to the selected academic year")

    section = None
    if academic_section_id not in (None, ""):
        section = db.session.get(AcademicSection, _require(academic_section_id, "Academic section"))
        if not section:
            raise EnrollmentValidationError("Academic section does not exist")
        if year_class and year_class.legacy_class_id != section.academic_class_id:
            raise EnrollmentValidationError("Academic section does not belong to the selected academic year class")

    enrollment_join = and_(
        StudentEnrollment.student_id == Student.id,
        StudentEnrollment.academic_year_id == year_id,
    )
    query = Student.query.outerjoin(StudentEnrollment, enrollment_join)

    enrollment_filters = [StudentEnrollment.id.isnot(None)]
    if year_level:
        enrollment_filters.append(StudentEnrollment.academic_year_level_id == year_level.id)
    if year_class:
        enrollment_filters.append(StudentEnrollment.academic_year_class_id == year_class.id)
    if section:
        enrollment_filters.append(StudentEnrollment.academic_section_id == section.id)
    enrolled_in_scope = and_(*enrollment_filters)

    legacy_filters = [Student.academic_year_id == year_id]
    if year_class:
        class_filters = []
        if year_class.legacy_class_id:
            class_filters.append(Student.academic_class_id == year_class.legacy_class_id)
            legacy_class = db.session.get(AcademicClass, year_class.legacy_class_id)
            school_class = (
                SchoolClass.query.filter_by(name=legacy_class.name).first()
                if legacy_class else None
            )
            if school_class:
                class_filters.append(and_(Student.academic_class_id.is_(None), Student.class_id == school_class.id))
        legacy_filters.append(or_(*class_filters) if class_filters else Student.id == -1)
    elif year_level:
        level_filters = []
        if year_level.legacy_level_id:
            level_filters.append(Student.academic_level_id == year_level.legacy_level_id)
        level_filters.append(and_(Student.academic_level_id.is_(None), Student.level == year_level.name))
        legacy_filters.append(or_(*level_filters))
    if section:
        legacy_filters.append(or_(
            Student.academic_section_id == section.id,
            and_(Student.academic_section_id.is_(None), Student.section == section.name),
        ))

    # The outer query already joins StudentEnrollment. Explicitly correlate
    # only Student so SQLAlchemy keeps StudentEnrollment in the EXISTS FROM
    # clause when the caller asks for count() or pagination.
    no_year_enrollment = ~exists().where(
        and_(
            StudentEnrollment.student_id == Student.id,
            StudentEnrollment.academic_year_id == year_id,
        )
    ).correlate(Student)
    return query.filter(or_(enrolled_in_scope, and_(no_year_enrollment, *legacy_filters)))


def student_enrollment_legacy_scope_query(
    academic_year_id,
    legacy_level_id=None,
    legacy_class_id=None,
    academic_section_id=None,
):
    """Resolve legacy selector IDs into the canonical year-aware scope."""
    year_id = _require(academic_year_id, "Academic year")
    year_level = None
    if legacy_level_id:
        year_level = AcademicYearLevel.query.filter_by(
            academic_year_id=year_id,
            legacy_level_id=_require(legacy_level_id, "Academic level"),
        ).first()
    year_class = None
    if legacy_class_id:
        class_query = (
            AcademicYearClass.query
            .join(AcademicYearLevel, AcademicYearLevel.id == AcademicYearClass.academic_year_level_id)
            .filter(
                AcademicYearLevel.academic_year_id == year_id,
                AcademicYearClass.legacy_class_id == _require(legacy_class_id, "Academic class"),
            )
        )
        if year_level:
            class_query = class_query.filter(AcademicYearClass.academic_year_level_id == year_level.id)
        year_class = class_query.first()
        year_level = year_level or (year_class.academic_year_level if year_class else None)
    if legacy_level_id and not year_level:
        raise EnrollmentValidationError("Academic level is not configured for this academic year")
    if legacy_class_id and not year_class:
        raise EnrollmentValidationError("Academic class is not configured for this academic year")
    return student_enrollment_scope_query(
        year_id,
        academic_year_level_id=year_level.id if year_level else None,
        academic_year_class_id=year_class.id if year_class else None,
        academic_section_id=academic_section_id,
    )


def enrollment_placement_for_student(student, academic_year_id):
    """Return the selected-year enrollment, or a legacy-compatible placement."""
    enrollment = get_enrollment_for_student_year(student.id, academic_year_id)
    if enrollment:
        return {
            "enrollment": enrollment,
            "academic_level_id": enrollment.academic_year_level.legacy_level_id if enrollment.academic_year_level else None,
            "academic_class_id": enrollment.academic_year_class.legacy_class_id if enrollment.academic_year_class else None,
            "academic_section_id": enrollment.academic_section_id,
            "class_name": enrollment.academic_year_class.name if enrollment.academic_year_class else None,
            "level_name": enrollment.academic_year_level.name if enrollment.academic_year_level else None,
            "section_name": enrollment.academic_section.name if enrollment.academic_section else None,
        }
    if student.academic_year_id == int(academic_year_id):
        return {
            "enrollment": None,
            "academic_level_id": student.academic_level_id,
            "academic_class_id": student.academic_class_id,
            "academic_section_id": student.academic_section_id,
            "class_name": student.academic_class.name if student.academic_class else (student.school_class.name if student.school_class else None),
            "level_name": student.level,
            "section_name": student.section,
        }
    return None


def create_enrollment(
    student_id,
    academic_year_id,
    academic_year_level_id,
    academic_year_class_id,
    academic_section_id=None,
    *,
    status="active",
    academic_outcome="pending",
    enrollment_source="manual",
    previous_enrollment_id=None,
    enrolled_at=None,
    exited_at=None,
    notes=None,
):
    """Create a validated enrollment without changing the Student record."""
    student_id = _require(student_id, "Student")
    student = db.session.get(Student, student_id)
    if not student:
        raise EnrollmentValidationError("Student does not exist")

    validate_enrollment_scope(
        academic_year_id,
        academic_year_level_id,
        academic_year_class_id,
        academic_section_id,
    )

    if status not in StudentEnrollment.STATUS_VALUES:
        raise EnrollmentValidationError("Invalid enrollment status")
    if academic_outcome not in StudentEnrollment.OUTCOME_VALUES:
        raise EnrollmentValidationError("Invalid academic outcome")
    if enrollment_source not in StudentEnrollment.SOURCE_VALUES:
        raise EnrollmentValidationError("Invalid enrollment source")

    if get_enrollment_for_student_year(student_id, academic_year_id):
        raise EnrollmentValidationError("Student already has an enrollment for this academic year")

    previous = None
    if previous_enrollment_id is not None:
        previous = db.session.get(StudentEnrollment, _require(previous_enrollment_id, "Previous enrollment"))
        if not previous:
            raise EnrollmentValidationError("Previous enrollment does not exist")
        if previous.student_id != student_id:
            raise EnrollmentValidationError("Previous enrollment belongs to another student")
        if previous.academic_year_id == int(academic_year_id):
            raise EnrollmentValidationError("Previous enrollment must belong to another academic year")

    enrollment = StudentEnrollment(
        student_id=student_id,
        academic_year_id=int(academic_year_id),
        academic_year_level_id=int(academic_year_level_id),
        academic_year_class_id=int(academic_year_class_id),
        academic_section_id=None if academic_section_id in (None, "") else int(academic_section_id),
        status=status,
        academic_outcome=academic_outcome,
        enrollment_source=enrollment_source,
        previous_enrollment_id=previous.id if previous else None,
        enrolled_at=enrolled_at or datetime.utcnow(),
        exited_at=exited_at,
        notes=notes,
    )
    db.session.add(enrollment)
    db.session.flush()
    return enrollment


TRANSITION_ACTIONS = {
    "transfer": {
        "source_status": "transferred",
        "source_outcome": "pending",
        "destination_source": "transfer",
    },
    "promotion": {
        "source_status": "completed",
        "source_outcome": "promoted",
        "destination_source": "promotion",
    },
    "repeat": {
        "source_status": "completed",
        "source_outcome": "repeated",
        "destination_source": "repeat",
    },
}


def _transition_action(action):
    action = str(action or "").strip().lower()
    if action not in TRANSITION_ACTIONS:
        raise EnrollmentValidationError("Choose transfer, promotion, or repeat")
    return action, TRANSITION_ACTIONS[action]


def _close_source_enrollment(source, action):
    """Close a source placement without changing its academic history."""
    _, settings = _transition_action(action)
    source.status = settings["source_status"]
    source.academic_outcome = settings["source_outcome"]
    source.exited_at = datetime.utcnow()


def transition_student_enrollment(
    student_id,
    source_enrollment_id,
    destination_academic_year_id,
    destination_academic_year_level_id,
    destination_academic_year_class_id,
    destination_academic_section_id=None,
    *,
    action="transfer",
    notes=None,
):
    """Create one destination enrollment and preserve the source enrollment.

    The caller owns the outer transaction/commit. A savepoint protects the
    multi-write operation so a failed flush cannot leave a half-transition.
    """
    action, settings = _transition_action(action)
    student_id = _require(student_id, "Student")
    source_id = _require(source_enrollment_id, "Source enrollment")
    student = db.session.get(Student, student_id)
    source = db.session.get(StudentEnrollment, source_id)
    if not student:
        raise EnrollmentValidationError("Student does not exist")
    if not source or source.student_id != student.id:
        raise EnrollmentValidationError("Source enrollment does not belong to this student")
    if source.status not in ("active", "completed"):
        raise EnrollmentValidationError("Source enrollment is not eligible for an academic transition")

    destination_scope = validate_enrollment_scope(
        destination_academic_year_id,
        destination_academic_year_level_id,
        destination_academic_year_class_id,
        destination_academic_section_id,
    )
    destination_year_id = destination_scope["academic_year"].id
    if source.academic_year_id == destination_year_id:
        raise EnrollmentValidationError("Destination must be a different academic year")
    if get_enrollment_for_student_year(student.id, destination_year_id):
        raise EnrollmentValidationError("Student already has an enrollment for the destination academic year")

    with db.session.begin_nested():
        destination = create_enrollment(
            student.id,
            destination_year_id,
            destination_scope["academic_year_level"].id,
            destination_scope["academic_year_class"].id,
            destination_scope["academic_section"].id if destination_scope["academic_section"] else None,
            status="active",
            academic_outcome="pending",
            enrollment_source=settings["destination_source"],
            previous_enrollment_id=source.id,
            notes=notes,
        )
        _close_source_enrollment(source, action)
        db.session.flush()
    return source, destination


def plan_bulk_transition(
    source_academic_year_id,
    source_academic_year_level_id,
    source_academic_year_class_id,
    destination_academic_year_id,
    destination_academic_year_level_id,
    destination_academic_year_class_id,
    *,
    source_academic_section_id=None,
    destination_academic_section_id=None,
):
    """Build a read-only bulk transition plan before any writes."""
    validate_enrollment_scope(
        source_academic_year_id,
        source_academic_year_level_id,
        source_academic_year_class_id,
        source_academic_section_id,
    )
    destination_scope = validate_enrollment_scope(
        destination_academic_year_id,
        destination_academic_year_level_id,
        destination_academic_year_class_id,
        destination_academic_section_id,
    )
    source_year_id = _require(source_academic_year_id, "Source academic year")
    destination_year_id = destination_scope["academic_year"].id
    if source_year_id == destination_year_id:
        raise EnrollmentValidationError("Destination must be a different academic year")

    query = StudentEnrollment.query.filter_by(
        academic_year_id=source_year_id,
        academic_year_level_id=_require(source_academic_year_level_id, "Source academic year level"),
        academic_year_class_id=_require(source_academic_year_class_id, "Source academic year class"),
    )
    if source_academic_section_id not in (None, ""):
        query = query.filter_by(academic_section_id=_require(source_academic_section_id, "Source academic section"))
    source_enrollments = query.order_by(StudentEnrollment.id).all()

    plan = []
    for source in source_enrollments:
        student = db.session.get(Student, source.student_id)
        existing = get_enrollment_for_student_year(source.student_id, destination_year_id)
        if existing:
            plan.append({
                "student": student,
                "source": source,
                "eligible": False,
                "reason": "already_enrolled",
                "existing_destination": existing,
            })
        elif not student:
            plan.append({
                "student": None,
                "source": source,
                "eligible": False,
                "reason": "missing_student",
                "existing_destination": None,
            })
        else:
            plan.append({
                "student": student,
                "source": source,
                "eligible": True,
                "reason": None,
                "existing_destination": None,
            })
    return {
        "source_enrollments": source_enrollments,
        "destination_scope": destination_scope,
        "items": plan,
    }


def execute_bulk_transition(plan, *, action="transfer", notes=None):
    """Execute a previously validated bulk plan atomically at the session level."""
    action, settings = _transition_action(action)
    created = []
    with db.session.begin_nested():
        for item in plan["items"]:
            if not item["eligible"]:
                continue
            source = item["source"]
            student = item["student"]
            scope = plan["destination_scope"]
            destination = create_enrollment(
                student.id,
                scope["academic_year"].id,
                scope["academic_year_level"].id,
                scope["academic_year_class"].id,
                scope["academic_section"].id if scope["academic_section"] else None,
                status="active",
                academic_outcome="pending",
                enrollment_source=settings["destination_source"],
                previous_enrollment_id=source.id,
                notes=notes,
            )
            _close_source_enrollment(source, action)
            created.append(destination)
        db.session.flush()
    return created


def apply_legacy_placement(student, scope):
    """Mirror a new enrollment into legacy fields for compatibility reads."""
    year_level = scope["academic_year_level"]
    year_class = scope["academic_year_class"]
    section = scope.get("academic_section")
    student.academic_level_id = year_level.legacy_level_id
    student.academic_class_id = year_class.legacy_class_id
    student.academic_section_id = section.id if section else None
    student.level = year_level.name
    student.section = section.name if section else None
    if year_class.legacy_class_id:
        legacy_class = db.session.get(AcademicClass, year_class.legacy_class_id)
        if legacy_class:
            school_class = SchoolClass.query.filter_by(name=legacy_class.name).first()
            if not school_class:
                school_class = SchoolClass(name=legacy_class.name)
                db.session.add(school_class)
                db.session.flush()
            student.class_id = school_class.id


def _candidate_year_level(student):
    if not student.academic_year_id:
        return [], "MISSING_YEAR"
    if not student.academic_level_id:
        return [], "MISSING_LEVEL_MAPPING"
    candidates = AcademicYearLevel.query.filter_by(
        academic_year_id=student.academic_year_id,
        legacy_level_id=student.academic_level_id,
    ).all()
    if len(candidates) == 0:
        return [], "MISSING_LEVEL_MAPPING"
    if len(candidates) > 1:
        return candidates, "AMBIGUOUS"
    return candidates, None


def _candidate_year_class(student, year_level):
    if not student.academic_class_id:
        return [], "MISSING_CLASS_MAPPING"
    candidates = AcademicYearClass.query.filter_by(
        academic_year_level_id=year_level.id,
        legacy_class_id=student.academic_class_id,
    ).all()
    if len(candidates) == 0:
        return [], "MISSING_CLASS_MAPPING"
    if len(candidates) > 1:
        return candidates, "AMBIGUOUS"
    return candidates, None


def _student_mapping(student):
    """Classify one legacy Student without writing anything."""
    base = {
        "student_id": student.id,
        "student_code": student.student_code,
        "student_name": student.full_name,
        "academic_year_id": student.academic_year_id,
        "legacy_level_id": student.academic_level_id,
        "legacy_class_id": student.academic_class_id,
        "legacy_section_id": student.academic_section_id,
        "candidate_year_level_id": None,
        "candidate_year_class_id": None,
        "classification": None,
        "reason": None,
        "recommended_action": None,
    }
    if not student.academic_year_id:
        base.update(classification="MISSING_YEAR", reason="Student has no academic year", recommended_action="Assign an academic year before backfill")
        return base

    level_candidates, level_error = _candidate_year_level(student)
    if level_error:
        base.update(
            classification=level_error,
            reason="No unique year-aware level mapping exists" if level_error == "AMBIGUOUS" else "Legacy level cannot be mapped to this academic year",
            recommended_action="Review the year-aware level mapping manually",
        )
        if level_candidates:
            base["candidate_year_level_id"] = [item.id for item in level_candidates]
        return base
    year_level = level_candidates[0]
    base["candidate_year_level_id"] = year_level.id

    class_candidates, class_error = _candidate_year_class(student, year_level)
    if class_error:
        base.update(
            classification=class_error,
            reason="No unique year-aware class mapping exists" if class_error == "AMBIGUOUS" else "Legacy class cannot be mapped to this year-aware level",
            recommended_action="Review the year-aware class mapping manually",
        )
        if class_candidates:
            base["candidate_year_class_id"] = [item.id for item in class_candidates]
        return base
    year_class = class_candidates[0]
    base["candidate_year_class_id"] = year_class.id

    if student.academic_section_id:
        section = db.session.get(AcademicSection, student.academic_section_id)
        if not section or year_class.legacy_class_id != section.academic_class_id:
            base.update(
                classification="SECTION_MAPPING_PROBLEM",
                reason="Legacy section does not belong to the mapped legacy class",
                recommended_action="Review the section mapping manually",
            )
            return base

    base.update(
        classification="READY_TO_BACKFILL",
        reason="Unique year, level, class, and optional section mapping found",
        recommended_action="Safe for controlled backfill",
    )
    return base


def dry_run_legacy_backfill(report_path=None):
    """Return and optionally write a machine-readable legacy mapping report."""
    entries = [_student_mapping(student) for student in Student.query.order_by(Student.id).all()]
    summary = {"total_students": len(entries)}
    for classification in (
        "READY_TO_BACKFILL",
        "AMBIGUOUS",
        "INVALID",
        "MISSING_YEAR",
        "MISSING_LEVEL_MAPPING",
        "MISSING_CLASS_MAPPING",
        "SECTION_MAPPING_PROBLEM",
    ):
        summary[classification.lower()] = sum(item["classification"] == classification for item in entries)
    report = {"generated_at": datetime.utcnow().isoformat() + "Z", "summary": summary, "students": entries}
    if report_path:
        path = Path(report_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    return report


def backfill_ready_students(report=None):
    """Insert only unambiguous mappings; unresolved students are untouched."""
    report = report or dry_run_legacy_backfill()
    backfilled = []
    excluded = []
    for item in report["students"]:
        if item["classification"] != "READY_TO_BACKFILL":
            excluded.append(item["student_id"])
            continue
        if get_enrollment_for_student_year(item["student_id"], item["academic_year_id"]):
            continue
        create_enrollment(
            item["student_id"],
            item["academic_year_id"],
            item["candidate_year_level_id"],
            item["candidate_year_class_id"],
            item["legacy_section_id"],
            enrollment_source="backfill",
        )
        backfilled.append(item["student_id"])
    db.session.commit()
    return {"backfilled_student_ids": backfilled, "excluded_student_ids": excluded}
