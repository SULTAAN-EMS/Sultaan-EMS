import re
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal

from flask import request
from flask_login import current_user

from . import db
from .models import Result, SchoolClass, Setting, Student, Subject, Teacher, TeacherActivity, TeacherCodeSequence, TeacherPermission, User
from .services import get_settings, grade_for

TEACHER_PERMISSIONS = [
    ("view_dashboard", "View Dashboard"),
    ("view_students", "View Students"),
    ("view_student_profiles", "View Student Profiles"),
    ("view_student_results", "View Student Results"),
    ("view_assigned_subjects", "View Assigned Subjects"),
    ("view_assigned_classes", "View Assigned Classes"),
    ("view_examinations", "View Examinations"),
    ("generate_reports", "Generate Reports"),
    ("export_excel", "Export Excel"),
    ("export_pdf", "Export PDF"),
    ("print_reports", "Print Reports"),
    ("future_ai_features", "Future AI Features"),
    ("future_analysis_features", "Future Analysis Features"),
]

# Backward compatibility for teachers assigned legacy permission keys.
TEACHER_PERMISSION_GROUPS = {
    "view_dashboard": {
        "view_dashboard",
        "view_examination_results",
    },
    "view_students": {
        "view_students",
        "view_assigned_students",
    },
    "view_student_profiles": {
        "view_student_profiles",
        "student_analysis",
    },
    "view_student_results": {
        "view_student_results",
        "view_examination_results",
    },
    "view_assigned_subjects": {"view_assigned_subjects"},
    "view_assigned_classes": {"view_assigned_classes"},
    "view_examinations": {
        "view_examinations",
        "view_examination_results",
    },
    "generate_reports": {
        "generate_reports",
        "reports",
    },
    "export_excel": {
        "export_excel",
        "download_reports",
    },
    "export_pdf": {
        "export_pdf",
        "download_reports",
    },
    "print_reports": {
        "print_reports",
        "reports",
    },
    "future_ai_features": {
        "future_ai_features",
        "ai_insights",
    },
    "future_analysis_features": {
        "future_analysis_features",
        "subject_analysis",
        "exam_comparison",
        "grade_distribution",
        "performance_trends",
        "student_ranking",
        "weak_students",
        "top_performers",
    },
}

SCHOOL_LEVELS = ["Primary", "Middle", "Secondary", "High School"]
EMPLOYMENT_TYPES = ["Full Time", "Part Time", "Contract"]

DEFAULT_TEACHER_SETTINGS = {
    "teacher_code_auto_generate": "on",
    "teacher_code_format": "TC-{seq:04d}",
    "teacher_default_username_format": "{teacher_id}",
    "teacher_password_min_length": "8",
    "teacher_default_password_policy": "alphanumeric",
    "teacher_theme": "light",
    "teacher_profile_photo_size": "medium",
    "teacher_max_upload_size": "5242880",
    "teacher_default_status": "Active",
    "teacher_card_style": "classic",
}


def get_teacher_settings():
    settings = get_settings()
    settings.update(DEFAULT_TEACHER_SETTINGS)
    rows = Setting.query.filter(Setting.key.like("teacher_%")).all()
    settings.update({row.key: row.value for row in rows})
    return settings


def seed_teacher_settings():
    for key, value in DEFAULT_TEACHER_SETTINGS.items():
        if not db.session.get(Setting, key):
            db.session.add(Setting(key=key, value=value))


def teacher_permission_set(teacher):
    return {row.permission for row in teacher.permission_rows}


def teacher_permission_aliases(permission):
    return TEACHER_PERMISSION_GROUPS.get(permission, {permission})


def teacher_has_permission(teacher, permission):
    assigned = teacher_permission_set(teacher)
    if not assigned:
        return True
    granted = set()
    for key in assigned:
        granted.update(teacher_permission_aliases(key))
    required = teacher_permission_aliases(permission)
    return bool(granted & required)


def set_teacher_permissions(teacher, permissions):
    TeacherPermission.query.filter_by(teacher_id=teacher.id).delete()
    for permission in sorted(set(permissions or [])):
        db.session.add(TeacherPermission(teacher_id=teacher.id, permission=permission))


def log_teacher_activity(teacher, action, details=""):
    forwarded_for = request.headers.get("X-Forwarded-For", "") if request else ""
    ip_address = forwarded_for.split(",")[0].strip() or (request.remote_addr if request else "")
    db.session.add(
        TeacherActivity(
            teacher_id=teacher.id,
            action=action,
            details=details,
            ip_address=ip_address,
        )
    )


def parse_code_format(format_string):
    match = re.search(r"\{seq:(\d+)d\}", format_string or "")
    if not match:
        return format_string or "TC-{seq:04d}", 4
    width = int(match.group(1))
    prefix = format_string[: match.start()]
    suffix = format_string[match.end() :]
    return prefix, width, suffix


def generate_teacher_code(manual_code=None):
    settings = get_teacher_settings()
    if manual_code:
        code = manual_code.strip()
        if Teacher.query.filter_by(teacher_code=code).first():
            raise ValueError(f"Teacher code already exists: {code}")
        return code
    if settings.get("teacher_code_auto_generate") != "on":
        raise ValueError("Teacher code is required when auto-generation is disabled.")
    format_string = settings.get("teacher_code_format") or "TC-{seq:04d}"
    match = re.search(r"\{seq:(\d+)d\}", format_string)
    if not match:
        raise ValueError("Teacher code format must include {seq:04d} style placeholder.")
    width = int(match.group(1))
    prefix = format_string[: match.start()]
    suffix = format_string[match.end() :]
    sequence = TeacherCodeSequence.query.filter_by(prefix=prefix).first()
    if not sequence:
        sequence = TeacherCodeSequence(prefix=prefix, last_number=0)
        db.session.add(sequence)
        db.session.flush()
    sequence.last_number += 1
    for _ in range(100):
        code = f"{prefix}{sequence.last_number:0{width}d}{suffix}"
        if not Teacher.query.filter_by(teacher_code=code).first():
            return code
        sequence.last_number += 1
    raise ValueError("Unable to generate a unique teacher code.")


def default_teacher_username(teacher):
    settings = get_teacher_settings()
    pattern = settings.get("teacher_default_username_format") or "{teacher_id}"
    return (
        pattern.replace("{teacher_id}", teacher.teacher_id)
        .replace("{teacher_code}", teacher.teacher_code)
        .replace("{email}", (teacher.email or "").split("@")[0])
        .strip()
        .lower()
    )


def validate_teacher_password(password):
    settings = get_teacher_settings()
    min_length = int(settings.get("teacher_password_min_length") or 8)
    if len(password) < min_length:
        return f"Password must be at least {min_length} characters."
    policy = settings.get("teacher_default_password_policy") or "alphanumeric"
    if policy == "alphanumeric" and not (re.search(r"[A-Za-z]", password) and re.search(r"\d", password)):
        return "Password must include letters and numbers."
    return None


def create_or_update_teacher_user(teacher, username, password, account_active, force_change):
    if not username and not teacher.user_id:
        return None
    user = db.session.get(User, teacher.user_id) if teacher.user_id else User()
    if username:
        existing = User.query.filter(User.username == username, User.id != user.id).first()
        if existing:
            raise ValueError(f"Username already exists: {username}")
        user.username = username.strip()
    user.full_name = teacher.full_name
    user.role = "staff"
    user.is_active = account_active
    if password:
        error = validate_teacher_password(password)
        if error:
            raise ValueError(error)
        user.set_password(password)
    elif not user.id:
        raise ValueError("Password is required for new teacher accounts.")
    db.session.add(user)
    db.session.flush()
    teacher.user_id = user.id
    teacher.force_password_change = force_change
    return user


def teacher_dashboard_stats():
    teachers = Teacher.query.all()
    active = [t for t in teachers if t.employment_status == "Active" and t.is_active]
    inactive = [t for t in teachers if t.employment_status == "Inactive" or not t.is_active]
    by_level = {level: 0 for level in SCHOOL_LEVELS}
    for teacher in teachers:
        if teacher.school_level:
            by_level[teacher.school_level] = by_level.get(teacher.school_level, 0) + 1
    subject_links = db.session.execute(db.text("SELECT COUNT(*) FROM teacher_subjects")).scalar() or 0
    class_links = db.session.execute(db.text("SELECT COUNT(*) FROM teacher_classes")).scalar() or 0
    departments = defaultdict(int)
    for teacher in teachers:
        if teacher.department:
            departments[teacher.department] += 1
    return {
        "total": len(teachers),
        "active": len(active),
        "inactive": len(inactive),
        "by_level": by_level,
        "subject_assignments": subject_links,
        "class_assignments": class_links,
        "departments": dict(departments),
        "recent": Teacher.query.order_by(Teacher.updated_at.desc()).limit(8).all(),
        "recent_activity": TeacherActivity.query.order_by(TeacherActivity.created_at.desc()).limit(10).all(),
    }


def teacher_list_query(filters):
    query = Teacher.query
    if filters.get("q"):
        q = f"%{filters['q']}%"
        query = query.filter(
            db.or_(
                Teacher.teacher_id.like(q),
                Teacher.teacher_code.like(q),
                Teacher.full_name.like(q),
                Teacher.phone.like(q),
                Teacher.email.like(q),
                Teacher.department.like(q),
            )
        )
    if filters.get("department"):
        query = query.filter(Teacher.department == filters["department"])
    if filters.get("school_level") in SCHOOL_LEVELS:
        query = query.filter(Teacher.school_level == filters["school_level"])
    if filters.get("employment_status") in ("Active", "Inactive"):
        query = query.filter(Teacher.employment_status == filters["employment_status"])
    if filters.get("subject_id") and str(filters["subject_id"]).isdigit():
        query = query.filter(Teacher.subjects.any(Subject.id == int(filters["subject_id"])))
    if filters.get("class_id") and str(filters["class_id"]).isdigit():
        query = query.filter(Teacher.classes.any(SchoolClass.id == int(filters["class_id"])))
    return query.order_by(Teacher.full_name.asc())


def teacher_performance_data(teacher=None):
    teachers = [teacher] if teacher else Teacher.query.filter_by(employment_status="Active").order_by(Teacher.full_name).all()
    payloads = []
    for item in teachers:
        class_ids = [school_class.id for school_class in item.classes]
        subject_ids = [subject.id for subject in item.subjects]
        students = Student.query.filter(Student.class_id.in_(class_ids)).all() if class_ids else []
        student_ids = [student.id for student in students]
        results = (
            Result.query.filter(Result.student_id.in_(student_ids), Result.subject_id.in_(subject_ids))
            .all()
            if student_ids and subject_ids
            else []
        )
        student_averages = {}
        for result in results:
            percentage = round(float(Decimal(result.score) / Decimal(result.subject.max_score) * 100), 2) if result.subject.max_score else 0
            student_averages.setdefault(result.student_id, []).append(percentage)
        pass_count = 0
        fail_count = 0
        for averages in student_averages.values():
            overall = sum(averages) / len(averages) if averages else Decimal("0")
            if grade_for(float(overall)).get("is_pass"):
                pass_count += 1
            else:
                fail_count += 1
        subject_totals = defaultdict(list)
        class_totals = defaultdict(list)
        for result in results:
            percentage = round(float(Decimal(result.score) / Decimal(result.subject.max_score) * 100), 2) if result.subject.max_score else 0.0
            subject_totals[result.subject.name].append(percentage)
            class_totals[result.student.school_class.name].append(percentage)
        subject_averages = {name: round(sum(values) / len(values), 2) for name, values in subject_totals.items() if values}
        class_averages = {name: round(sum(values) / len(values), 2) for name, values in class_totals.items() if values}
        best_class = max(class_averages, key=class_averages.get) if class_averages else None
        lowest_class = min(class_averages, key=class_averages.get) if class_averages else None
        total_students = len(student_averages)
        payloads.append(
            {
                "teacher": item,
                "assigned_subjects": [subject.name for subject in item.subjects],
                "assigned_classes": [school_class.name for school_class in item.classes],
                "pass_rate": round(pass_count / total_students * 100, 2) if total_students else 0,
                "fail_rate": round(fail_count / total_students * 100, 2) if total_students else 0,
                "subject_average": round(sum(subject_averages.values()) / len(subject_averages), 2) if subject_averages else 0,
                "subject_averages": subject_averages,
                "best_class": best_class,
                "lowest_class": lowest_class,
                "class_averages": class_averages,
                "student_count": total_students,
            }
        )
    return payloads


def teacher_activity_summary(teacher):
    activities = teacher.activities[:20]
    latest_by_action = {}
    for activity in teacher.activities:
        latest_by_action.setdefault(activity.action, activity)
    return {
        "teacher": teacher,
        "last_login": teacher.last_login_at,
        "last_logout": teacher.last_logout_at,
        "recent_actions": activities,
        "last_analysis": latest_by_action.get("Analysis"),
        "last_report": latest_by_action.get("Report Generated"),
        "last_download": latest_by_action.get("Download"),
    }


def parse_teacher_form_dates(value):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date() if value else None
    except ValueError:
        return None


def audit_teacher_action(action, details):
    from .audit import audit

    username = current_user.username if current_user.is_authenticated else "system"
    audit(action, details)
    return username


def authenticate_teacher_login(username, password):
    user = User.query.filter_by(username=username.strip(), is_active=True).first()
    if not user or not user.check_password(password):
        return None, None, "Invalid username or password."
    teacher = getattr(user, "teacher_profile", None)
    if not teacher or teacher.user_id != user.id:
        return None, None, "This account is not authorized for the Teacher Portal."
    if not teacher.is_active or teacher.employment_status != "Active":
        return None, None, "Your account is inactive. Please contact the school administrator."
    return user, teacher, None


def record_teacher_login(teacher):
    teacher.last_login_at = datetime.utcnow()
    log_teacher_activity(teacher, "Login", "Teacher signed in to the portal")


def record_teacher_logout(teacher):
    teacher.last_logout_at = datetime.utcnow()
    log_teacher_activity(teacher, "Logout", "Teacher signed out of the portal")


def request_teacher_password_reset(username):
    user = User.query.filter_by(username=username.strip()).first()
    teacher = getattr(user, "teacher_profile", None) if user else None
    if teacher and teacher.user_id == user.id:
        log_teacher_activity(teacher, "Password Reset Request", f"Reset requested for {username}")
        return teacher
    return None
