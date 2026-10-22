from flask_login import current_user

from . import db
from .models import (
    AcademicYearClass, AcademicYearLevel, AcademicYearSubject,
    Student, TeacherResultEvent, TeacherResultMarkRevision,
)


def record_teacher_result_mark_revision(
    mark,
    previous_score,
    next_score,
    action,
    *,
    is_change,
    entry_status,
    submission_attempt=None,
):
    """Append a point-in-time score transition to the caller's transaction."""
    if not action:
        return None
    actor = current_user if current_user.is_authenticated else None
    student = mark.student or db.session.get(Student, mark.student_id)
    if student is None:
        raise ValueError("Cannot record a teacher mark revision without its student.")
    submission = mark.submission
    db.session.add(TeacherResultMarkRevision(
        mark=mark,
        submission=submission,
        assignment=submission.assignment,
        student=student,
        actor=actor,
        student_name=student.full_name,
        student_code=student.student_code,
        actor_name=(actor.full_name or actor.username) if actor else "N/A",
        previous_score=previous_score,
        next_score=next_score,
        action=action,
        is_change=bool(is_change),
        entry_status=entry_status,
        submission_attempt=submission_attempt,
    ))


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
