from flask_login import current_user

from . import db
from .models import AcademicYearClass, AcademicYearLevel, AcademicYearSubject, TeacherResultEvent


def record_teacher_result_event(
    assignment,
    event_type,
    *,
    submission=None,
    status_from=None,
    status_to=None,
    marks_count=0,
    note=None,
):
    """Queue an immutable history snapshot in the caller's transaction."""
    academic_year_id = assignment.academic_year_id or assignment.academic_year.id
    academic_class_id = assignment.class_id or assignment.school_class.id
    subject_id = assignment.subject_id or assignment.subject.id
    year_level = AcademicYearLevel.query.filter_by(
        academic_year_id=academic_year_id,
        legacy_level_id=assignment.school_class.academic_level_id,
    ).order_by(AcademicYearLevel.sort_order, AcademicYearLevel.id).first()
    year_class = AcademicYearClass.query.filter_by(
        academic_year_level_id=year_level.id if year_level else None,
        legacy_class_id=academic_class_id,
    ).order_by(AcademicYearClass.sort_order, AcademicYearClass.id).first() if year_level else None
    year_subject = AcademicYearSubject.query.filter_by(
        academic_year_id=academic_year_id,
        academic_year_level_id=year_level.id if year_level else None,
        legacy_subject_id=subject_id,
        subject_kind="exam",
    ).order_by(AcademicYearSubject.sort_order, AcademicYearSubject.id).first() if year_level else None
    actor = current_user if current_user.is_authenticated else None

    db.session.add(TeacherResultEvent(
        assignment=assignment,
        submission=submission,
        teacher=assignment.teacher,
        actor=actor,
        event_type=event_type,
        status_from=status_from,
        status_to=status_to,
        marks_count=max(0, int(marks_count or 0)),
        note=(note or "").strip()[:500] or None,
        teacher_name=assignment.teacher.full_name,
        teacher_code=assignment.teacher.teacher_code,
        actor_name=(actor.full_name or actor.username) if actor else None,
        academic_year_name=assignment.academic_year.name,
        exam_name=assignment.exam.name,
        level_name=(year_level.name if year_level else assignment.school_class.academic_level.name if assignment.school_class.academic_level else ""),
        class_name=year_class.name if year_class else assignment.school_class.name,
        subject_name=year_subject.name if year_subject else assignment.subject.name,
    ))
