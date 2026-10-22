from functools import wraps
from urllib.parse import urlparse, urlunparse

from flask import abort, flash, redirect, request, url_for
from flask_login import current_user

from . import db
from .teacher_services import (
    TEACHER_PERMISSIONS,
    get_teacher_settings,
    log_teacher_activity,
    teacher_has_permission,
    teacher_permission_set,
)

TEACHER_PUBLIC_ENDPOINTS = frozenset(
    {
        "teacher_portal.login",
        "teacher_portal.forgot_password",
    }
)


def get_current_teacher():
    if not current_user.is_authenticated:
        return None
    return getattr(current_user, "teacher_profile", None)


def is_teacher_account():
    teacher = get_current_teacher()
    return bool(
        teacher
        and teacher.is_active
        and teacher.employment_status == "Active"
        and getattr(teacher, "user_id", None) == current_user.id
    )


def teacher_can(permission):
    teacher = get_current_teacher()
    if not teacher:
        return False
    return teacher_has_permission(teacher, permission)


def safe_teacher_next_url(next_url):
    """Validate that the next URL is safe to redirect to (prevents open redirect attacks)"""
    if not next_url:
        return url_for("teacher_portal.result_entry")
    
    # Parse the next URL
    parsed_next = urlparse(next_url)
    
    # Get the current host from the request
    request_host = request.host.split(':')[0]  # Remove port if present
    
    # If the next URL has a netloc (host), verify it matches the current host
    if parsed_next.netloc:
        next_host = parsed_next.netloc.split(':')[0]  # Remove port if present
        if next_host != request_host:
            # Host mismatch, redirect to dashboard instead
            return url_for("teacher_portal.result_entry")
    
    # If the URL is relative or has a matching host, it's safe
    return next_url


def teacher_requires_password_change():
    teacher = get_current_teacher()
    return bool(teacher and teacher.force_password_change)


def teacher_permission_required(permission):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated:
                return redirect(url_for("teacher_portal.login", next=request.url))
            if not is_teacher_account():
                flash("This area is only available to teacher accounts.", "danger")
                return redirect(url_for("teacher_portal.login"))
            if teacher_requires_password_change() and request.endpoint != "teacher_portal.change_password":
                flash("Please change your password before continuing.", "warning")
                return redirect(url_for("teacher_portal.change_password"))
            if not teacher_can(permission):
                flash("You do not have permission to access this feature.", "danger")
                return redirect(url_for("teacher_portal.dashboard"))
            return view(*args, **kwargs)

        return wrapped

    return decorator


def teacher_login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for("teacher_portal.login", next=request.url))
        if not is_teacher_account():
            flash("This area is only available to teacher accounts.", "danger")
            return redirect(url_for("teacher_portal.login"))
        if teacher_requires_password_change() and request.endpoint != "teacher_portal.change_password":
            flash("Please change your password before continuing.", "warning")
            return redirect(url_for("teacher_portal.change_password"))
        return view(*args, **kwargs)

    return wrapped


def ensure_teacher_resource(teacher, class_id=None, subject_id=None, student_id=None):
    class_ids = {school_class.id for school_class in teacher.classes}
    subject_ids = {subject.id for subject in teacher.subjects}
    if class_id is not None and int(class_id) not in class_ids:
        abort(403)
    if subject_id is not None and int(subject_id) not in subject_ids:
        abort(403)
    if student_id is not None:
        from .models import Student

        student = Student.query.get_or_404(int(student_id))
        if student.class_id not in class_ids:
            abort(403)
    return True


def parse_teacher_filters(args):
    def _int(value):
        return int(value) if value and str(value).isdigit() else None

    return {
        "academic_year_id": _int(args.get("academic_year_id")),
        "exam_id": _int(args.get("exam_id")),
        "subject_id": _int(args.get("subject_id")),
        "class_id": _int(args.get("class_id")),
        "section": (args.get("section") or "").strip() or None,
        "student_id": _int(args.get("student_id")),
        "date_from": (args.get("date_from") or "").strip() or None,
        "date_to": (args.get("date_to") or "").strip() or None,
        "q": (args.get("q") or "").strip() or None,
        "limit": _int(args.get("limit")) or 10,
        "compare_exam_id": _int(args.get("compare_exam_id")),
        "compare_class_id": _int(args.get("compare_class_id")),
        "compare_section": (args.get("compare_section") or "").strip() or None,
        "compare_year_id": _int(args.get("compare_year_id")),
        "compare_subject_id": _int(args.get("compare_subject_id")),
        "compare_type": (args.get("compare_type") or "exam").strip(),
    }


def record_teacher_visit(teacher, page_name):
    log_teacher_activity(teacher, "Analysis", f"Viewed {page_name}")


def teacher_theme():
    settings = get_teacher_settings()
    return settings.get("teacher_theme") or "light"


def all_teacher_permission_keys():
    return [key for key, _ in TEACHER_PERMISSIONS]
