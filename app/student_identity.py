"""Canonical student-ID resolution shared by admin and public surfaces."""

from sqlalchemy import func

from .models import Student, StudentCodeAlias


def normalize_student_code(value):
    """Normalize an ID for case-insensitive, whitespace-tolerant matching."""
    return str(value or "").strip().casefold()


def find_student_by_code(value):
    """Find a student by the current ID or any retained historical ID alias."""
    normalized = normalize_student_code(value)
    if not normalized:
        return None

    student = Student.query.filter(
        func.lower(func.trim(Student.student_code)) == normalized
    ).first()
    if student:
        return student

    alias = StudentCodeAlias.query.filter(
        func.lower(func.trim(StudentCodeAlias.old_code)) == normalized
    ).first()
    return alias.student if alias else None


def student_code_taken(value, *, exclude_student_id=None):
    """Return whether a current ID or historical alias belongs to another student."""
    normalized = normalize_student_code(value)
    if not normalized:
        return False

    current_query = Student.query.filter(
        func.lower(func.trim(Student.student_code)) == normalized
    )
    if exclude_student_id is not None:
        current_query = current_query.filter(Student.id != exclude_student_id)
    if current_query.first():
        return True

    alias_query = StudentCodeAlias.query.filter(
        func.lower(func.trim(StudentCodeAlias.old_code)) == normalized
    )
    if exclude_student_id is not None:
        alias_query = alias_query.filter(StudentCodeAlias.student_id != exclude_student_id)
    return alias_query.first() is not None
