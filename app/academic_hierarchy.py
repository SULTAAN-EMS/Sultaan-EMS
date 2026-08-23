"""Phase 1D year-aware academic hierarchy helpers.

The legacy hierarchy is deliberately retained.  These helpers provide the
canonical year-scoped records and a narrow compatibility bridge for existing
students/results that still point at legacy IDs.
"""

from collections import defaultdict
from pathlib import Path
import json

from sqlalchemy import and_, or_

from . import db
from .models import (
    AcademicClass,
    AcademicLevel,
    AcademicYear,
    AcademicYearClass,
    AcademicYearLevel,
    AcademicYearSubject,
    Exam,
    Result,
    SchoolClass,
    Student,
    Subject,
)


def year_levels(year_id, active_only=True):
    query = AcademicYearLevel.query.filter_by(academic_year_id=year_id)
    if active_only:
        query = query.filter_by(is_active=True)
    return query.order_by(AcademicYearLevel.sort_order, AcademicYearLevel.name, AcademicYearLevel.id).all()


def year_classes(year_level_id, active_only=True):
    query = AcademicYearClass.query.filter_by(academic_year_level_id=year_level_id)
    if active_only:
        query = query.filter_by(is_active=True)
    return query.order_by(AcademicYearClass.sort_order, AcademicYearClass.name, AcademicYearClass.id).all()


def year_subjects(year_id, year_level_id=None, active_only=True):
    query = AcademicYearSubject.query.filter_by(academic_year_id=year_id)
    if year_level_id:
        query = query.filter_by(academic_year_level_id=year_level_id)
    if active_only:
        query = query.filter_by(is_active=True)
    return query.order_by(AcademicYearSubject.sort_order, AcademicYearSubject.name, AcademicYearSubject.id).all()


def validate_year_level(year_id, year_level_id):
    """Return a year-level only when it belongs to the selected year."""
    if not year_id or not year_level_id:
        return None
    return AcademicYearLevel.query.filter_by(
        id=year_level_id,
        academic_year_id=year_id,
    ).first()


def validate_year_subject(year_id, year_level_id):
    """Validate both sides of the year/level relationship."""
    year_level = validate_year_level(year_id, year_level_id)
    if not year_level:
        return None
    return year_level


def students_for_year_scope_query(year_id, year_level_id=None, year_class_id=None, section_id=None):
    """Return students in a year-aware scope with legacy fallback matching."""
    query = Student.query.filter(Student.academic_year_id == year_id)
    year_class = db.session.get(AcademicYearClass, year_class_id) if year_class_id else None
    year_level = (
        db.session.get(AcademicYearLevel, year_level_id)
        if year_level_id
        else (year_class.academic_year_level if year_class else None)
    )

    if year_class:
        legacy_class_id = year_class.legacy_class_id
        class_filters = []
        if legacy_class_id:
            class_filters.append(Student.academic_class_id == legacy_class_id)
            legacy_class = db.session.get(AcademicClass, legacy_class_id)
            school_class = (
                SchoolClass.query.filter_by(name=legacy_class.name).first()
                if legacy_class
                else None
            )
            if school_class:
                class_filters.append(
                    and_(Student.academic_class_id.is_(None), Student.class_id == school_class.id)
                )
        if class_filters:
            query = query.filter(or_(*class_filters))
    elif year_level:
        legacy_level_id = year_level.legacy_level_id
        level_filters = []
        if legacy_level_id:
            level_filters.append(Student.academic_level_id == legacy_level_id)
        level_filters.append(
            and_(Student.academic_level_id.is_(None), Student.level == year_level.name)
        )
        query = query.filter(or_(*level_filters))

    if section_id:
        from .models import AcademicSection
        section = db.session.get(AcademicSection, section_id)
        if section:
            section_filters = [Student.academic_section_id == section.id]
            section_filters.append(
                and_(Student.academic_section_id.is_(None), Student.section == section.name)
            )
            query = query.filter(or_(*section_filters))

    return query


def _get_or_create_year_level(year, legacy_level, report):
    existing = AcademicYearLevel.query.filter_by(
        academic_year_id=year.id,
        name=legacy_level.name,
    ).first()
    if existing:
        if existing.legacy_level_id is None:
            existing.legacy_level_id = legacy_level.id
        return existing
    level = AcademicYearLevel(
        academic_year_id=year.id,
        legacy_level_id=legacy_level.id,
        name=legacy_level.name,
        sort_order=legacy_level.sort_order,
        is_active=legacy_level.is_active,
    )
    db.session.add(level)
    db.session.flush()
    report["mapped_levels"].append({"year_id": year.id, "year_level_id": level.id, "legacy_level_id": legacy_level.id})
    return level


def _get_or_create_year_class(year_level, legacy_class, report):
    existing = AcademicYearClass.query.filter_by(
        academic_year_level_id=year_level.id,
        name=legacy_class.name,
    ).first()
    if existing:
        if existing.legacy_class_id is None:
            existing.legacy_class_id = legacy_class.id
        return existing
    item = AcademicYearClass(
        academic_year_level_id=year_level.id,
        legacy_class_id=legacy_class.id,
        name=legacy_class.name,
        sort_order=legacy_class.sort_order,
        is_active=legacy_class.is_active,
    )
    db.session.add(item)
    db.session.flush()
    report["mapped_classes"].append({"year_class_id": item.id, "legacy_class_id": legacy_class.id})
    return item


def _get_or_create_year_subject(year, year_level, legacy_subject, report):
    existing = AcademicYearSubject.query.filter_by(
        academic_year_id=year.id,
        academic_year_level_id=year_level.id,
        name=legacy_subject.name,
    ).first()
    if existing:
        if existing.legacy_subject_id is None:
            existing.legacy_subject_id = legacy_subject.id
        return existing
    item = AcademicYearSubject(
        academic_year_id=year.id,
        academic_year_level_id=year_level.id,
        legacy_subject_id=legacy_subject.id,
        name=legacy_subject.name,
        max_score=legacy_subject.max_score,
        sort_order=legacy_subject.sort_order,
        is_active=legacy_subject.is_active,
    )
    db.session.add(item)
    db.session.flush()
    report["mapped_subjects"].append({"year_subject_id": item.id, "legacy_subject_id": legacy_subject.id})
    return item


def backfill_year_hierarchy(report_path=None):
    """Map only unambiguous legacy usage into the new year-aware tables.

    Results whose student's level disagrees with the subject's level are
    reported, never rewritten.  This keeps the known AF-SOOMAALI exceptions
    available for manual review.
    """
    report = {
        "mapped_levels": [],
        "mapped_classes": [],
        "mapped_subjects": [],
        "ambiguous": [],
        "cross_level_results": [],
    }
    for year in AcademicYear.query.order_by(AcademicYear.id).all():
        level_ids = set(
            value
            for value, in db.session.query(Student.academic_level_id)
            .filter(Student.academic_year_id == year.id, Student.academic_level_id.isnot(None))
            .distinct()
            .all()
        )
        level_ids.update(
            value
            for value, in db.session.query(Exam.academic_level_id)
            .filter(Exam.academic_year_id == year.id, Exam.academic_level_id.isnot(None))
            .distinct()
            .all()
        )

        year_level_by_legacy = {}
        for legacy_level_id in sorted(level_ids):
            legacy_level = db.session.get(AcademicLevel, legacy_level_id)
            if legacy_level:
                year_level_by_legacy[legacy_level.id] = _get_or_create_year_level(year, legacy_level, report)

        class_ids = set(
            value
            for value, in db.session.query(Student.academic_class_id)
            .filter(Student.academic_year_id == year.id, Student.academic_class_id.isnot(None))
            .distinct()
            .all()
        )
        for legacy_class_id in sorted(class_ids):
            legacy_class = db.session.get(AcademicClass, legacy_class_id)
            if not legacy_class:
                continue
            year_level = year_level_by_legacy.get(legacy_class.academic_level_id)
            if year_level:
                _get_or_create_year_class(year_level, legacy_class, report)

        subject_ids = set(
            value
            for value, in db.session.query(Result.subject_id)
            .join(Exam, Result.exam_id == Exam.id)
            .filter(Exam.academic_year_id == year.id)
            .distinct()
            .all()
        )
        for legacy_subject_id in sorted(subject_ids):
            legacy_subject = db.session.get(Subject, legacy_subject_id)
            if not legacy_subject or not legacy_subject.academic_level_id:
                report["ambiguous"].append({"kind": "subject", "id": legacy_subject_id, "year_id": year.id})
                continue
            year_level = year_level_by_legacy.get(legacy_subject.academic_level_id)
            if year_level:
                _get_or_create_year_subject(year, year_level, legacy_subject, report)

        for result, student, subject in (
            db.session.query(Result, Student, Subject)
            .join(Student, Result.student_id == Student.id)
            .join(Subject, Result.subject_id == Subject.id)
            .join(Exam, Result.exam_id == Exam.id)
            .filter(Exam.academic_year_id == year.id)
            .all()
        ):
            if (
                student.academic_level_id
                and subject.academic_level_id
                and student.academic_level_id != subject.academic_level_id
            ):
                report["cross_level_results"].append({
                    "result_id": result.id,
                    "student_id": student.id,
                    "subject_id": subject.id,
                    "year_id": year.id,
                })

    db.session.commit()
    if report_path:
        path = Path(report_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    return report
