import json
from datetime import datetime, date, timedelta
from functools import wraps

from flask import Blueprint, abort, current_app, flash, jsonify, redirect, render_template, request, send_file, session, url_for
from flask_login import current_user, login_required
from openpyxl import Workbook
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from werkzeug.utils import secure_filename

from . import db
from .audit import audit
from .cloudinary_service import upload_image
from .import_wizard import process_result_import, process_student_import, result_entry_import_template, student_template
from .models import AcademicLevel, AcademicClass, AcademicSection, AcademicYear, AttendanceRecord, AuditLog, Exam, GradeScale, IncidentAction, IncidentAttachment, IncidentCategory, IncidentReport, ReportVerification, Result, SchoolClass, SeverityLevel, Setting, Student, Subject, User, ExamInvigilator, InvigilatorLoginHistory, IncidentReportSettings, IdCardIssue
from .permissions import PERMISSIONS, can, enforce_endpoint_permission, permission_required
from .security import ALLOWED_AUDIO, ALLOWED_PHOTOS, ALLOWED_SHEETS, allowed_file
from .services import (
    PROMOTION_COPY_MARKERS,
    RESULT_SUCCESS_OVERLAY_LABEL_DEFAULTS,
    RESULT_SUCCESS_TEMPLATE_DEFAULTS,
    RESULT_SUCCESS_TEMPLATE_ORDER,
    get_settings,
    grade_for,
    result_payload,
    result_success_overlay_settings as get_result_success_overlay_settings,
    result_success_overlay_labels,
    result_success_position_tier,
    result_success_template_copy,
    subject_icon,
    validate_admin_password,
)
from .services import slug

admin_bp = Blueprint("admin", __name__)


def _top10_tunnel_tracks(raw_value):
    """Read the small, persistent Top 10 music library defensively."""
    try:
        payload = json.loads(raw_value or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    if not isinstance(payload, list):
        return []
    tracks = []
    for entry in payload:
        if not isinstance(entry, dict) or not isinstance(entry.get("url"), str):
            continue
        tracks.append({
            "url": entry["url"],
            "name": str(entry.get("name") or "Tunnel music"),
            "uploaded_at": str(entry.get("uploaded_at") or ""),
        })
    return tracks


INCIDENT_SETTING_DEFAULTS = [
    ("invigilator_password_type", "letters_numbers", "string", "security", "Invigilator password character type"),
    ("invigilator_password_min_length", "6", "integer", "security", "Invigilator password minimum length"),
    ("allow_signature_reuse", "true", "boolean", "security", "Allow invigilators to reuse saved digital signatures"),
    ("reset_require_username", "true", "boolean", "security", "Require username for invigilator password reset"),
    ("reset_require_invigilator_id", "true", "boolean", "security", "Require Invigilator ID for password reset"),
    ("reset_require_phone", "false", "boolean", "security", "Require phone number for password reset when available"),
    ("invigilator_session_timeout", "30", "integer", "security", "Invigilator session timeout in minutes"),
    ("invigilator_idle_timeout", "30", "integer", "security", "Invigilator idle timeout in minutes"),
    ("invigilator_remember_duration", "1440", "integer", "security", "Remember login duration in minutes"),
    ("incident_reference_prefix", "INC", "string", "general", "Incident reference prefix"),
    ("incident_image_display_size", "medium", "string", "general", "Incident image display size"),
    ("require_category", "true", "boolean", "requirements", "Incident Category"),
    ("require_severity", "true", "boolean", "requirements", "Severity Level"),
    ("require_description", "true", "boolean", "requirements", "Description"),
    ("require_signature", "false", "boolean", "requirements", "Digital Signature"),
    ("require_evidence", "false", "boolean", "requirements", "Evidence Upload"),
    ("require_subject", "false", "boolean", "requirements", "Subject"),
    ("require_actions_taken", "false", "boolean", "requirements", "Actions Taken"),
    ("require_incident_date", "true", "boolean", "requirements", "Incident Date"),
    ("require_incident_time", "true", "boolean", "requirements", "Incident Time"),
]


def ensure_incident_setting_defaults():
    changed = False
    for key, value, setting_type, category, description in INCIDENT_SETTING_DEFAULTS:
        if not IncidentReportSettings.query.filter_by(setting_key=key).first():
            db.session.add(IncidentReportSettings(
                setting_key=key,
                setting_value=value,
                setting_type=setting_type,
                category=category,
                description=description,
            ))
            changed = True
    if changed:
        db.session.commit()


def incident_setting_value(key, default=None):
    row = IncidentReportSettings.query.filter_by(setting_key=key).first()
    return row.setting_value if row else default


def incident_bool_setting(key, default=False):
    return str(incident_setting_value(key, "true" if default else "false")).lower() == "true"


def validate_invigilator_password(password):
    policy_type = incident_setting_value("invigilator_password_type", "letters_numbers")
    min_length = int(incident_setting_value("invigilator_password_min_length", "6") or 6)
    if len(password or "") < min_length:
        return False, f"Password must be at least {min_length} characters."
    if policy_type == "numbers" and not password.isdigit():
        return False, "Password must contain numbers only."
    if policy_type == "letters" and not password.isalpha():
        return False, "Password must contain letters only."
    if policy_type == "letters_numbers" and not password.isalnum():
        return False, "Password must contain letters and numbers only."
    return True, ""


def generate_invigilator_password():
    import random
    import string

    policy_type = incident_setting_value("invigilator_password_type", "letters_numbers")
    min_length = int(incident_setting_value("invigilator_password_min_length", "6") or 6)
    alphabet = string.ascii_letters + string.digits
    if policy_type == "numbers":
        alphabet = string.digits
    elif policy_type == "letters":
        alphabet = string.ascii_letters
    return "".join(random.choice(alphabet) for _ in range(max(4, min_length)))


@admin_bp.before_request
@login_required
def require_login():
    enforce_endpoint_permission()


@admin_bp.route("/")
def dashboard():
    current_year = AcademicYear.query.filter_by(is_current=True).first()
    stats = {
        "students": Student.query.count(),
        "classes": SchoolClass.query.count(),
        "exams": Exam.query.count(),
        "published": Exam.query.filter_by(is_published=True).count(),
        "subjects": Subject.query.count(),
        "locked": Student.query.filter_by(is_result_locked=True).count(),
    }
    latest_results = Result.query.order_by(Result.updated_at.desc()).limit(10).all()
    return render_template("admin/dashboard.html", stats=stats, current_year=current_year, latest_results=latest_results)


@admin_bp.route("/incidents")
def incidents():
    """Incident Reports Dashboard"""
    q = request.args.get("q", "").strip()
    status_filter = request.args.get("status", "")
    severity_filter = request.args.get("severity", "")
    category_filter = request.args.get("category", "")
    room_filter = request.args.get("room", "").strip()
    subject_filter = request.args.get("subject", "").strip()
    session_filter = request.args.get("exam_session", "")
    academic_year_filter = request.args.get("academic_year_id", "")
    exam_filter = request.args.get("exam_id", "") or session_filter
    level_filter = request.args.get("level_id", "")
    class_filter = request.args.get("class_id", "")
    date_from = request.args.get("date_from", "")
    date_to = request.args.get("date_to", "")

    query = IncidentReport.query.outerjoin(IncidentReport.student).outerjoin(IncidentReport.exam)

    if q:
        query = query.filter(
            or_(
                Student.student_code.like(f"%{q}%"),
                Student.full_name.like(f"%{q}%")
            )
        )

    if status_filter:
        query = query.filter(IncidentReport.status == status_filter)

    if severity_filter:
        query = query.filter(IncidentReport.severity_id == int(severity_filter))

    if category_filter:
        query = query.filter(IncidentReport.category_id == int(category_filter))

    if room_filter:
        query = query.filter(IncidentReport.exam_room.like(f"%{room_filter}%"))

    if subject_filter:
        query = query.filter(IncidentReport.subject.has(Subject.name.like(f"%{subject_filter}%")))

    if academic_year_filter:
        query = query.filter(Exam.academic_year_id == int(academic_year_filter))

    if exam_filter:
        query = query.filter(IncidentReport.exam_id == int(exam_filter))

    if level_filter:
        query = query.filter(Student.academic_level_id == int(level_filter))

    if class_filter:
        query = query.filter(Student.academic_class_id == int(class_filter))

    if date_from:
        query = query.filter(IncidentReport.incident_date >= datetime.strptime(date_from, "%Y-%m-%d").date())

    if date_to:
        query = query.filter(IncidentReport.incident_date <= datetime.strptime(date_to, "%Y-%m-%d").date())

    reports = query.order_by(IncidentReport.created_at.desc()).all()
    # Statistics reflect the currently filtered report set.
    stats = {
        "total": len(reports),
        "critical": sum(1 for r in reports if r.severity and r.severity.name.lower() == "critical"),
        "serious": sum(1 for r in reports if r.severity and r.severity.name.lower() == "serious"),
        "moderate": sum(1 for r in reports if r.severity and r.severity.name.lower() == "moderate"),
        "minor": sum(1 for r in reports if r.severity and r.severity.name.lower() == "minor"),
        "resolved": sum(1 for r in reports if r.status == "Resolved"),
    }
    categories = IncidentCategory.query.filter_by(is_active=True).order_by(IncidentCategory.sort_order).all()
    severities = SeverityLevel.query.filter_by(is_active=True).order_by(SeverityLevel.sort_order).all()
    academic_years = AcademicYear.query.order_by(AcademicYear.name.desc()).all()
    exams = Exam.query.order_by(Exam.id.desc()).all()
    levels = AcademicLevel.query.order_by(AcademicLevel.sort_order, AcademicLevel.name).all()
    classes = AcademicClass.query.order_by(AcademicClass.sort_order, AcademicClass.name).all()
    exam_sessions = exams

    return render_template(
        "admin/incidents.html",
        reports=reports,
        stats=stats,
        categories=categories,
        severities=severities,
        academic_years=academic_years,
        exams=exams,
        levels=levels,
        classes=classes,
        exam_sessions=exam_sessions,
        session_filter=session_filter,
        academic_year_filter=academic_year_filter,
        exam_filter=exam_filter,
        level_filter=level_filter,
        class_filter=class_filter,
        room_filter=room_filter,
        subject_filter=subject_filter,
        q=q,
        status_filter=status_filter,
        severity_filter=severity_filter,
        category_filter=category_filter,
        date_from=date_from,
        date_to=date_to
    )


@admin_bp.route("/incidents/<int:report_id>")
def incident_view(report_id):
    """View single incident report details"""
    ensure_incident_setting_defaults()
    report = IncidentReport.query.get_or_404(report_id)
    categories = IncidentCategory.query.filter_by(is_active=True).order_by(IncidentCategory.sort_order).all()
    severities = SeverityLevel.query.filter_by(is_active=True).order_by(SeverityLevel.sort_order).all()
    image_display_size = incident_setting_value("incident_image_display_size", "medium")
    return render_template(
        "admin/incident_view.html",
        report=report,
        categories=categories,
        severities=severities,
        image_display_size=image_display_size,
    )


@admin_bp.route("/incidents/<int:report_id>/edit", methods=["GET", "POST"])
def incident_edit(report_id):
    """Edit incident report"""
    report = IncidentReport.query.get_or_404(report_id)

    if request.method == "POST":
        report.category_id = int(request.form.get("category_id"))
        report.severity_id = int(request.form.get("severity_id"))
        report.exam_room = request.form.get("exam_room", "")
        report.description = request.form.get("description", "")
        report.actions_taken = request.form.get("actions_taken", "")
        report.status = request.form.get("status", "Pending Review")
        report.reviewed_by_id = current_user.id
        report.reviewed_at = datetime.utcnow()
        report.review_notes = request.form.get("review_notes", "")

        db.session.commit()
        audit("Incident Report Updated", f"Updated report {report.report_number}")
        flash("Incident report updated successfully.", "success")
        return redirect(url_for("admin.incidents"))

    categories = IncidentCategory.query.filter_by(is_active=True).order_by(IncidentCategory.sort_order).all()
    severities = SeverityLevel.query.filter_by(is_active=True).order_by(SeverityLevel.sort_order).all()
    return render_template("admin/incident_edit.html", report=report, categories=categories, severities=severities)


@admin_bp.route("/incidents/<int:report_id>/delete", methods=["POST"])
def incident_delete(report_id):
    """Delete incident report"""
    report = IncidentReport.query.get_or_404(report_id)
    report_number = report.report_number
    db.session.delete(report)
    db.session.commit()
    audit("Incident Report Deleted", f"Deleted report {report_number}")
    flash("Incident report deleted successfully.", "success")
    return redirect(url_for("admin.incidents"))


@admin_bp.route("/students")
def students():
    return redirect(url_for("admin_advanced_results.students_management", **request.args.to_dict(flat=True)))


@admin_bp.route("/students/new", methods=["GET", "POST"])
@admin_bp.route("/students/<int:student_id>/edit", methods=["GET", "POST"])
def student_form(student_id=None):
    endpoint = "admin_advanced_results.student_form"
    target = url_for(endpoint, student_id=student_id) if student_id else url_for(endpoint)
    return redirect(target, code=307 if request.method == "POST" else 302)


@admin_bp.route("/students/<int:student_id>/delete", methods=["POST"])
def delete_student(student_id):
    return redirect(url_for("admin_advanced_results.delete_student", student_id=student_id), code=307)


@admin_bp.route("/students/<int:student_id>/toggle-lock", methods=["POST"])
def toggle_student_lock(student_id):
    return redirect(url_for("admin_advanced_results.toggle_student_lock", student_id=student_id), code=307)


@admin_bp.route("/students/import", methods=["POST"])
def import_students():
    return redirect(url_for("admin_advanced_results.import_students"), code=307)


@admin_bp.route("/students/import/confirm", methods=["POST"])
def confirm_student_import():
    return redirect(url_for("admin_advanced_results.confirm_student_import"), code=307)


@admin_bp.route("/students/import/template")
def student_import_template():
    return redirect(url_for("admin_advanced_results.student_import_template"))


@admin_bp.route("/students/export")
def export_students():
    return redirect(url_for("admin_advanced_results.export_students"))


@admin_bp.route("/results")
def results():
    """Redirect to new Results dashboard - old view retired"""
    return redirect(url_for("admin_advanced_results.new_dashboard"))


@admin_bp.route("/results/save", methods=["POST"])
def save_results():
    student = db.session.get(Student, int(request.form["student_id"])) or abort_404()
    exam = db.session.get(Exam, int(request.form["exam_id"])) or abort_404()
    for subject in Subject.query.order_by(Subject.sort_order, Subject.name).all():
        raw = request.form.get(f"subject_{subject.id}", "").strip()
        if raw == "":
            continue
        score = max(0, min(float(raw), float(subject.max_score)))
        result = Result.query.filter_by(student_id=student.id, exam_id=exam.id, subject_id=subject.id).first() or Result(
            student=student, exam=exam, subject=subject
        )
        result.score = score
        result.is_published = bool(request.form.get("is_published"))
        db.session.add(result)
    audit("Result Publishing", f"Saved results for student {student.student_code} exam {exam.name}")
    db.session.commit()
    flash("Results saved.", "success")
    return redirect(url_for("admin_advanced_results.new_dashboard"))


@admin_bp.route("/results/<int:result_id>/delete", methods=["POST"])
def delete_result(result_id):
    result = db.session.get(Result, result_id) or abort_404()
    db.session.delete(result)
    audit("Result Publishing", f"Deleted result row {result_id}")
    db.session.commit()
    flash("Result row deleted.", "success")
    return redirect(url_for("admin_advanced_results.new_dashboard"))


@admin_bp.route("/results/<int:student_id>/<int:exam_id>/edit", methods=["GET", "POST"])
def edit_result_set(student_id, exam_id):
    student = db.session.get(Student, student_id) or abort_404()
    exam = db.session.get(Exam, exam_id) or abort_404()
    subjects = Subject.query.order_by(Subject.sort_order, Subject.name).all()
    existing = {row.subject_id: row for row in Result.query.filter_by(student_id=student.id, exam_id=exam.id).all()}
    if request.method == "POST":
        changes = []
        for subject in subjects:
            raw = request.form.get(f"score_{subject.id}", "").strip()
            result = existing.get(subject.id)
            if raw == "":
                continue
            score = max(0, min(float(raw), float(subject.max_score)))
            if not result:
                result = Result(student=student, exam=exam, subject=subject)
            old_score = float(result.score) if result.id and result.score is not None else None
            old_grade = result.grade_override or ""
            old_comment = result.comment or ""
            result.score = score
            result.grade_override = request.form.get(f"grade_{subject.id}", "").strip() or None
            result.comment = request.form.get(f"comment_{subject.id}", "").strip() or None
            result.is_published = bool(request.form.get(f"published_{subject.id}"))
            db.session.add(result)
            if old_score != score or old_grade != (result.grade_override or "") or old_comment != (result.comment or ""):
                changes.append(f"{subject.name}: {old_score} -> {score}")
        audit("Result Publishing", f"Edited result set for {student.student_code} / {exam.name}: {', '.join(changes) or 'no score changes'}")
        db.session.commit()
        flash("Result changes saved. Totals, averages, grades, and status recalculated automatically.", "success")
        return redirect(url_for("admin.edit_result_set", student_id=student.id, exam_id=exam.id))
    payload = result_payload(student, exam=exam, public_only=False)
    subject_previews = {}
    for subject in subjects:
        row = existing.get(subject.id)
        percentage = round(float(row.score) / float(subject.max_score) * 100, 2) if row and subject.max_score else 0
        subject_previews[subject.id] = grade_for(percentage)
    return render_template(
        "admin/result_edit.html",
        student=student,
        exam=exam,
        subjects=subjects,
        existing=existing,
        subject_previews=subject_previews,
        payload=payload,
        scales=GradeScale.query.order_by(GradeScale.min_score.desc()).all(),
    )


@admin_bp.route("/results/import", methods=["POST"])
def import_results():
    return redirect(url_for("admin_advanced_results.import_results"), code=307)


@admin_bp.route("/results/import/template")
def result_import_template():
    return redirect(url_for("admin_advanced_results.result_import_template"))


@admin_bp.route("/results/export")
def export_results():
    wb = Workbook()
    ws = wb.active
    ws.title = "Results"
    ws.append(["student_id", "student_name", "exam", "subject", "score", "published"])
    for r in Result.query.join(Result.student).order_by(Student.full_name).all():
        ws.append([r.student.student_code, r.student.full_name, r.exam.name, r.subject.name, float(r.score), r.is_published])
    return workbook_response(wb, "results.xlsx")


@admin_bp.route("/reports/<int:student_id>/<int:exam_id>")
def admin_print_report(student_id, exam_id):
    student = db.session.get(Student, student_id) or abort_404()
    exam = db.session.get(Exam, exam_id) or abort_404()
    payload = result_payload(student, exam=exam, public_only=False)
    from .verification import verification_payload

    payload["verification"] = verification_payload(student, exam)
    payload["generated_at"] = datetime.now()
    db.session.commit()
    return render_template("print_report.html", result=payload)


@admin_bp.route("/classes", methods=["GET", "POST"])
def classes():
    if request.method == "POST":
        cls = db.session.get(SchoolClass, int(request.form.get("id") or 0)) or SchoolClass()
        cls.name = request.form["name"].strip()
        cls.description = request.form.get("description", "").strip()
        db.session.add(cls)
        db.session.commit()
        flash("Class saved.", "success")
        return redirect(url_for("admin_advanced_results.new_dashboard"))
    return redirect(url_for("admin_advanced_results.new_dashboard"))


@admin_bp.route("/subjects", methods=["GET", "POST"])
def subjects():
    if request.method == "POST":
        subject = db.session.get(Subject, int(request.form.get("id") or 0)) or Subject()
        subject.name = request.form["name"].strip()
        subject.max_score = float(request.form.get("max_score", 100))
        subject.sort_order = int(request.form.get("sort_order", 0))
        db.session.add(subject)
        db.session.commit()
        flash("Subject saved.", "success")
        return redirect(url_for("admin_advanced_results.new_setup"))
    return redirect(url_for("admin_advanced_results.new_setup"))


@admin_bp.route("/exams", methods=["GET", "POST"])
def exams():
    if request.method == "POST":
        exam = db.session.get(Exam, int(request.form.get("id") or 0)) or Exam()
        exam.name = request.form["name"].strip()
        exam.short_code = request.form.get("short_code", "").strip()
        exam.academic_year_id = int(request.form["academic_year_id"])
        exam.weight_percentage = float(request.form.get("weight_percentage", 0))
        exam.sort_order = int(request.form.get("sort_order", 0))
        exam.is_active = bool(request.form.get("is_active"))
        exam.is_published = bool(request.form.get("is_published"))
        db.session.add(exam)
        db.session.commit()
        flash("Exam saved.", "success")
        return redirect(url_for("admin_advanced_results.new_setup"))
    return redirect(url_for("admin_advanced_results.new_setup"))


@admin_bp.route("/exams/<int:row_id>/delete", methods=["POST"])
def delete_exam(row_id):
    return delete_row(Exam, row_id, "admin_advanced_results.new_setup")


@admin_bp.route("/exams/<int:row_id>/toggle", methods=["POST"])
def toggle_exam(row_id):
    exam = db.session.get(Exam, row_id) or abort_404()
    exam.is_published = not exam.is_published
    Result.query.filter_by(exam_id=exam.id).update({"is_published": exam.is_published})
    db.session.commit()
    flash("Publish status updated.", "success")
    return redirect(url_for("admin_advanced_results.new_setup"))


@admin_bp.route("/academic-years", methods=["GET", "POST"])
def academic_years():
    if request.method == "POST":
        year = db.session.get(AcademicYear, int(request.form.get("id") or 0)) or AcademicYear()
        year.name = request.form["name"].strip()
        db.session.add(year)
        db.session.commit()
        flash("Academic year saved.", "success")
        return redirect(url_for("admin_advanced_results.new_dashboard"))
    return redirect(url_for("admin_advanced_results.new_dashboard"))


@admin_bp.route("/academic-years/<int:row_id>/current", methods=["POST"])
def switch_year(row_id):
    AcademicYear.query.update({"is_current": False})
    year = db.session.get(AcademicYear, row_id) or abort_404()
    year.is_current = True
    db.session.commit()
    flash("Current academic year switched.", "success")
    return redirect(url_for("admin_advanced_results.new_dashboard"))


@admin_bp.route("/users", methods=["GET", "POST"])
@permission_required("users")
def users():
    if request.method == "POST":
        user = db.session.get(User, int(request.form.get("id") or 0)) or User()
        user.username = request.form["username"].strip()
        user.full_name = request.form["full_name"].strip()
        user.role = request.form["role"]
        user.is_active = bool(request.form.get("is_active"))
        photo = request.files.get("photo")
        if photo and photo.filename:
            if not allowed_file(photo.filename, ALLOWED_PHOTOS):
                flash("Admin photo must be JPG, PNG, or WEBP.", "danger")
                return redirect(url_for("admin.users"))
            photo_url = upload_image(photo, "school/admins")
            user.photo_path = photo_url
        password = request.form.get("password", "")
        if password:
            valid_password, password_error = validate_admin_password(password)
            if not valid_password:
                flash(password_error, "danger")
                return redirect(url_for("admin.users"))
            user.set_password(password)
        elif not user.id:
            flash("Password is required for new users.", "danger")
            return redirect(url_for("admin.users"))
        db.session.add(user)
        posted_permissions = request.form.getlist("permissions")
        user.set_permissions(posted_permissions)
        audit("Admin Updates", f"Saved admin {user.username}")
        audit("Permission Changes", f"Updated permissions for {user.username}: {', '.join(posted_permissions)}")
        db.session.commit()
        flash("User saved.", "success")
        return redirect(url_for("admin.users"))
    return render_template("admin/users.html", rows=User.query.order_by(User.username).all(), permissions=PERMISSIONS)


@admin_bp.route("/users/<int:row_id>/reset", methods=["POST"])
@permission_required("users")
def reset_user_password(row_id):
    user = db.session.get(User, row_id) or abort_404()
    password = request.form.get("password", "")
    valid_password, password_error = validate_admin_password(password)
    if not valid_password:
        flash(password_error, "danger")
    else:
        user.set_password(password)
        audit("Admin Updates", f"Reset password for {user.username}")
        db.session.commit()
        flash("Password reset.", "success")
    return redirect(url_for("admin.users"))


@admin_bp.route("/settings", methods=["GET", "POST"])
def settings():
    if request.method == "POST":
        editable_keys = [
            "school_name",
            "school_address",
            "school_phone",
            "school_email",
            "school_website",
            "school_motto",
            "principal_name",
            "school_footer",
            "dashboard_title",
            "dashboard_subtitle",
            "dashboard_theme",
            "primary_color",
            "secondary_color",
            "sidebar_color",
            "visible_cards",
            "homepage_widgets",
            "search_footer_text",
            "search_footer_font_size",
            "search_footer_font_weight",
            "search_footer_text_color",
            "search_footer_background_color",
            "search_footer_border_color",
            "search_footer_visibility",
            "typography_page_title_size",
            "typography_subtitle_size",
            "typography_input_label_size",
            "typography_input_placeholder_size",
            "typography_button_size",
            "typography_footer_size",
            "typography_copyright_size",
            "typography_student_info_size",
            "typography_dashboard_heading_size",
            "typography_table_text_size",
            "default_language",
            "whatsapp_url",
            "facebook_url",
            "instagram_url",
            "telegram_url",
            "twitter_url",
            "email_url",
            "call_url",
            "maps_url",
            "enable_phone_verification",
            "report_header_style",
            "report_footer_text",
            "report_primary_color",
            "report_accent_color",
            "report_font_family",
            "report_border_style",
            "report_background",
            "report_watermark",
            "report_logo_position",
            "report_qr_position",
            "report_photo_position",
            "report_signature_position",
            "report_comment_box",
            "report_table_style",
            "principal_comment",
            "student_photo_shape",
            "student_photo_size",
            "student_photo_border",
            "student_photo_shadow",
            "attendance_color_present",
            "attendance_color_absent",
            "attendance_color_late",
            "attendance_color_excused",
            "attendance_color_medical_leave",
            "attendance_color_blocked",
            "attendance_icon_present",
            "attendance_icon_absent",
            "attendance_icon_late",
            "attendance_icon_excused",
            "attendance_icon_medical_leave",
            "attendance_icon_blocked",
            "result_page_primary_color",
            "result_page_accent_color",
            "result_dashboard_primary_color",
            "result_dashboard_secondary_color",
            "result_dashboard_accent_color",
            "result_dashboard_background_color",
            "result_dashboard_card_color",
            "result_dashboard_button_color",
            "result_dashboard_header_color",
            "result_dashboard_footer_color",
            "result_dashboard_table_header_color",
            "result_dashboard_text_color",
            "result_dashboard_muted_text_color",
            "result_dashboard_font_family",
            "result_dashboard_base_font_size",
            "result_dashboard_font_weight",
            "result_dashboard_line_height",
            "result_dashboard_border_radius",
            "result_dashboard_card_spacing",
            "result_dashboard_shadow",
            "result_dashboard_padding",
            "result_dashboard_margin",
            "result_dashboard_show_student_photo",
            "result_dashboard_show_school_logo",
            "result_dashboard_show_qr",
            "result_dashboard_show_sidebar",
            "result_dashboard_show_grade_scale",
            "result_dashboard_show_performance",
            "result_dashboard_show_teacher_remarks",
            "result_dashboard_show_summary",
            "result_dashboard_show_footer",
            "result_dashboard_show_social_icons",
            "result_dashboard_show_download_button",
            "result_dashboard_show_print_button",
            "result_dashboard_show_share_button",
            "result_dashboard_show_top10_button",
            "result_sidebar_theme",
            "result_sidebar_student_name_size",
            "result_sidebar_student_name_weight",
            "result_sidebar_label_size",
            "result_sidebar_value_size",
            "result_sidebar_title_size",
            "result_sidebar_school_name_size",
            "result_sidebar_school_motto_size",
            "result_sidebar_photo_border_width",
            "result_sidebar_photo_border_color",
            "result_sidebar_photo_border_style",
            "result_sidebar_photo_border_radius",
            "result_sidebar_photo_width",
            "result_sidebar_photo_height",
            "result_sidebar_photo_object_fit",
            "result_sidebar_photo_object_position",
            "result_sidebar_photo_shadow",
            "result_sidebar_label_color",
            "result_sidebar_label_font_weight",
            "result_sidebar_label_letter_spacing",
            "result_sidebar_label_text_transform",
            "result_sidebar_value_color",
            "result_sidebar_value_weight",
            "result_sidebar_show_student_photo",
            "result_sidebar_show_school_logo",
            "result_sidebar_show_overlay_logo",
            "result_sidebar_show_student_name",
            "result_sidebar_show_student_class",
            "result_sidebar_show_parent_name",
            "result_sidebar_show_student_id",
            "result_sidebar_show_exam_name",
            "result_sidebar_show_download_date",
            "result_sidebar_show_percentage",
            "result_sidebar_show_school_name",
            "result_sidebar_show_school_motto",
            "result_table_style",
            "result_card_style",
            "result_button_style",
            "result_icon_style",
            "result_online_title_primary",
            "result_online_title_accent",
            "result_online_quote",
            "result_label_mother_name",
            "result_label_student_id",
            "result_label_student_class",
            "result_label_exam_type",
            "result_label_date_issued",
            "result_label_subject_percentage",
            "result_academic_summary_title",
            "result_teacher_remarks_title",
            "result_footer_owner",
            "download_datetime_format",
            "print_brand_code",
            "print_report_title",
            "print_exam_banner_text",
            "print_subtitle",
            "print_student_heading",
            "print_marks_heading",
            "print_qr_label",
            "print_comments_heading",
            "print_signature_title",
            "print_signature_subtitle",
            "print_footer_owner",
            "print_layout_header_color",
            "print_layout_banner_color",
            "print_layout_banner_accent",
            "print_layout_table_header_color",
            "print_layout_border_color",
            "print_layout_background_color",
            "print_layout_text_color",
            "print_layout_font_family",
            "print_layout_font_size",
            "print_layout_font_weight",
            "print_layout_margin",
            "print_layout_padding",
            "print_layout_radius",
            "print_layout_shadow",
            "print_layout_table_row_height",
            "print_layout_table_font_size",
            "print_layout_page_spacing",
            "print_show_school_logo",
            "print_show_school_name",
            "print_show_academic_year_badge",
            "print_show_exam_banner",
            "print_show_student_photo",
            "print_show_qr_code",
            "print_show_download_date",
            "print_show_teacher_signature",
            "print_show_principal_signature",
            "print_show_footer",
            "print_show_watermark",
            "verify_page_enabled",
            "verify_page_title",
            "verify_page_subtitle",
            "verify_page_footer_text",
            "verify_page_copyright_text",
            "verify_success_message",
            "verify_badge_style",
            "verify_theme",
            "verify_school_motto",
            "verify_primary_color",
            "verify_secondary_color",
            "verify_accent_color",
            "verify_background_color",
            "verify_card_color",
            "verify_success_color",
            "verify_text_color",
            "verify_muted_text_color",
            "verify_button_color",
            "verify_border_color",
            "verify_icon_color",
            "verify_font_family",
            "verify_font_size",
            "verify_font_weight",
            "verify_card_radius",
            "verify_card_shadow",
            "verify_spacing",
            "verify_page_width",
            "verify_section_order",
            "verify_show_student_photo",
            "verify_show_school_logo",
            "verify_show_badge",
            "verify_show_digital_seal",
            "verify_show_result_summary",
            "verify_show_details",
            "verify_show_footer",
            "verify_fields_student_name",
            "verify_fields_student_id",
            "verify_fields_class",
            "verify_fields_exam",
            "verify_fields_academic_year",
            "verify_fields_total_marks",
            "verify_fields_percentage",
            "verify_fields_grade",
            "verify_fields_rank",
            "verify_fields_status",
            "verify_message",
            "verify_badge_text",
            "verify_status_text",
            "verify_id_prefix",
            "verify_footer_heading",
            "verify_animation_success",
            "verify_animation_loading",
            "verify_design_preset",
            "verify_animation_fade",
            "verify_animation_zoom",
            "verify_animation_slide",
            "verify_animation_bounce",
            "verify_animation_ripple",
            "verify_animation_glow",
            "verify_custom_css",
            "verify_custom_js",
            "verify_id_header_primary",
            "verify_id_header_secondary",
            "verify_id_badge_primary",
            "verify_id_badge_secondary",
            "verify_id_logo_size",
            "verify_id_header_padding",
            "verify_id_photo_size",
            "verify_id_photo_border_width",
            "verify_id_photo_border_color",
            "verify_id_photo_radius",
            "verify_id_card_radius",
            "verify_id_card_padding",
            "verify_id_card_spacing",
            "verify_id_stamp_size",
            "verify_id_stamp_color",
            "verify_id_status_color",
            "verify_id_status_dark",
            "verify_id_badge_radius",
            "verify_id_badge_animation",
            "verify_id_glass_effect",
            "verify_id_show_watermark",
            "verify_id_photo_shadow",
            "verify_id_show_header",
            "verify_id_show_badge",
            "verify_id_show_status_card",
            "verify_id_show_verification_area",
            "verify_id_show_footer",
            "verify_id_show_icons",
            "verify_id_show_background_decorations",
            "verify_id_show_logo",
            "verify_id_show_photo",
            "verify_id_show_student_name",
            "verify_id_show_student_id",
            "verify_id_show_mother_name",
            "verify_id_show_class",
            "verify_id_show_section",
            "verify_id_show_academic_year",
            "verify_id_show_exam_type",
            "verify_id_show_issue_date",
            "verify_id_show_expiry_date",
            "verify_id_show_stamp",
            "verify_id_show_verification_code",
            "verify_id_show_date_time",
            "verify_id_font_family",
            "verify_id_font_size",
            "verify_id_font_weight",
            "verify_id_text_color",
            "verify_id_letter_spacing",
            "verify_id_line_height",
            "verify_id_text_align",
            "verify_id_header_font_size",
            "verify_id_name_font_size",
            "verify_id_label_font_size",
            "verify_id_value_font_size",
            "verify_id_bg_color",
            "verify_id_card_bg",
            "verify_id_border_color",
            "verify_id_footer_bg",
            "verify_id_footer_text_color",
            "verify_id_badge_text_color",
            "verify_id_status_text_color",
            "verify_id_shadow_color",
            "verify_id_template_style",
        ]
        for key in editable_keys:
            setting = db.session.get(Setting, key) or Setting(key=key)
            setting.value = request.form.get(key, "").strip()
            db.session.add(setting)
        upload_fields = {
            "logo": "logo_path",
            "admin_logo": "admin_logo_path",
            "dashboard_background": "dashboard_background",
            "principal_signature": "principal_signature_path",
            "vice_principal_signature": "vice_principal_signature_path",
            "exam_officer_signature": "exam_officer_signature_path",
            "school_stamp": "school_stamp_path",
            "result_dashboard_background_image": "result_dashboard_background_image",
            "result_dashboard_default_avatar": "result_dashboard_default_avatar",
            "result_dashboard_footer_logo": "result_dashboard_footer_logo",
            "print_background_image": "print_background_image",
            "print_watermark_image": "print_watermark_image",
            "print_footer_logo": "print_footer_logo",
            "verify_background_image": "verify_background_image",
            "verify_default_student_photo": "verify_default_student_photo",
            "footer_logo": "footer_logo_path",
        }
        for field_name, setting_key in upload_fields.items():
            upload = request.files.get(field_name)
            if not upload or not upload.filename:
                continue
            if not allowed_file(upload.filename, ALLOWED_PHOTOS):
                if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                    return jsonify({"success": False, "message": "Uploaded images must be JPG, PNG, or WEBP."}), 400
                flash("Uploaded images must be JPG, PNG, or WEBP.", "danger")
                return redirect(url_for("admin.settings"))
            image_url = upload_image(upload, "school/settings")
            setting = db.session.get(Setting, setting_key) or Setting(key=setting_key)
            setting.value = image_url
            db.session.add(setting)
        music_upload = request.files.get("top10_tunnel_background_music")
        if music_upload and music_upload.filename:
            if not allowed_file(music_upload.filename, ALLOWED_AUDIO):
                message = "Tunnel background music must be an MP3 or WAV file."
                if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                    return jsonify({"success": False, "message": message}), 400
                flash(message, "danger")
                return redirect(url_for("admin.settings"))
            music_upload.stream.seek(0, 2)
            music_size = music_upload.stream.tell()
            music_upload.stream.seek(0)
            if music_size > 10 * 1024 * 1024:
                message = "Tunnel background music must be 10 MB or smaller."
                if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                    return jsonify({"success": False, "message": message}), 400
                flash(message, "danger")
                return redirect(url_for("admin.settings"))
            music_url = upload_image(music_upload, "school/top10-tunnel")
            setting = db.session.get(Setting, "top10_tunnel_music_path") or Setting(key="top10_tunnel_music_path")
            previous_music_url = setting.value
            setting.value = music_url
            db.session.add(setting)
            library_setting = db.session.get(Setting, "top10_tunnel_music_tracks") or Setting(key="top10_tunnel_music_tracks")
            tracks = _top10_tunnel_tracks(library_setting.value)
            # Preserve an old single-track setup when the library is introduced.
            if not tracks and previous_music_url and previous_music_url != music_url:
                tracks.append({"url": previous_music_url, "name": "Existing custom track", "uploaded_at": ""})
            tracks.append({
                "url": music_url,
                "name": secure_filename(music_upload.filename) or "Tunnel music",
                "uploaded_at": datetime.now().strftime("%B %d, %Y %H:%M"),
            })
            # Retain a deliberate, ordered library without unbounded settings data.
            library_setting.value = json.dumps(tracks[-10:])
            db.session.add(library_setting)
        for subject in Subject.query.order_by(Subject.name).all():
            field_name = f"subject_icon_{subject.id}"
            upload = request.files.get(field_name)
            if not upload or not upload.filename:
                continue
            if not allowed_file(upload.filename, ALLOWED_PHOTOS):
                if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                    return jsonify({"success": False, "message": "Uploaded subject icons must be JPG, PNG, or WEBP."}), 400
                flash("Uploaded subject icons must be JPG, PNG, or WEBP.", "danger")
                return redirect(url_for("admin.settings"))
            image_url = upload_image(upload, "school/subjects")
            setting_key = f"subject_icon_{slug(subject.name)}"
            setting = db.session.get(Setting, setting_key) or Setting(key=setting_key)
            setting.value = image_url
            db.session.add(setting)
        # NOTE: Grade Management (GradeScale) is saved by its own dedicated
        # route. It must NEVER be written here — the settings form does not
        # contain grade fields, so any attempt to read them from request.form
        # would silently corrupt every grade record (is_pass → False,
        # is_active → False, etc.) on every Admin Settings save.

        audit("Settings Changes", "Updated system settings")
        db.session.commit()
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"success": True, "message": "Settings saved."})
        flash("Settings saved.", "success")
        return redirect(url_for("admin.settings"))
    current_settings = get_settings()
    return render_template(
        "admin/settings.html",
        settings=current_settings,
        top10_tunnel_tracks=_top10_tunnel_tracks(current_settings.get("top10_tunnel_music_tracks")),
        scales=GradeScale.query.order_by(GradeScale.sort_order.asc(), GradeScale.min_score.desc()).all(),
        subjects=Subject.query.order_by(Subject.name).all(),
        slug=slug,
    )


@admin_bp.route("/settings/top10-tunnel-music/reset", methods=["POST"])
@permission_required("settings")
def reset_top10_tunnel_music():
    """Revert the public tunnel to the bundled default music track."""
    setting = db.session.get(Setting, "top10_tunnel_music_path") or Setting(key="top10_tunnel_music_path")
    setting.value = ""
    db.session.add(setting)
    # Also clear the tracks library so the tunnel stops reading uploaded tracks
    library_setting = db.session.get(Setting, "top10_tunnel_music_tracks") or Setting(key="top10_tunnel_music_tracks")
    library_setting.value = "[]"
    db.session.add(library_setting)
    audit("Tunnel Music Reset", "Restored the default Top 10 tunnel background music")
    db.session.commit()
    return jsonify(success=True, message="Default tunnel music restored.")


@admin_bp.route("/settings/top10-tunnel-music/delete-track", methods=["POST"])
@permission_required("settings")
def delete_top10_tunnel_track():
    """Permanently remove an uploaded track from the tunnel music library."""
    track_url = request.json.get("url", "").strip() if request.is_json else ""
    if not track_url:
        return jsonify(success=False, message="No track URL provided."), 400

    library_setting = db.session.get(Setting, "top10_tunnel_music_tracks") or Setting(key="top10_tunnel_music_tracks")
    tracks = _top10_tunnel_tracks(library_setting.value)
    original_count = len(tracks)
    tracks = [t for t in tracks if t["url"] != track_url]
    if len(tracks) == original_count:
        return jsonify(success=False, message="Track not found in library."), 404

    library_setting.value = json.dumps(tracks)
    db.session.add(library_setting)

    # If the deleted track was the active one, clear the active path
    path_setting = db.session.get(Setting, "top10_tunnel_music_path") or Setting(key="top10_tunnel_music_path")
    if path_setting.value == track_url:
        path_setting.value = tracks[-1]["url"] if tracks else ""
        db.session.add(path_setting)

    # Try to delete from Cloudinary storage
    try:
        import cloudinary
        import cloudinary.uploader
        from flask import current_app
        if current_app.config.get("CLOUDINARY_CLOUD_NAME"):
            cloudinary.config(
                cloud_name=current_app.config["CLOUDINARY_CLOUD_NAME"],
                api_key=current_app.config["CLOUDINARY_API_KEY"],
                api_secret=current_app.config["CLOUDINARY_API_SECRET"],
                secure=True,
            )
            # Extract public_id from the Cloudinary URL
            # URL format: https://res.cloudinary.com/<cloud>/video/upload/v.../folder/filename.ext
            parts = track_url.split("/upload/")
            if len(parts) == 2:
                public_id = parts[1].rsplit(".", 1)[0]  # Remove file extension
                if "/" in public_id:
                    public_id = "/".join(public_id.split("/")[1:])  # Remove version segment
                cloudinary.uploader.destroy(public_id, resource_type="video")
    except Exception:
        pass  # Best-effort deletion from storage; track is already removed from library

    audit("Tunnel Music Track Deleted", f"Removed track from library: {track_url}")
    db.session.commit()
    return jsonify(success=True, message="Track permanently deleted.")


LOADER_DESIGN_IDS = (20, 36, 38, 42)
LOADER_LOCKED_DESIGN_ID = 42
LOADER_DELETABLE_DESIGN_IDS = frozenset({20, 36, 38})


def _normalise_loader_configuration(settings):
    """Keep persisted loader preferences inside the supported four-design catalog."""
    try:
        deleted = {int(item) for item in json.loads(settings.get("admin_loader_deleted_designs") or "[]")}
    except (TypeError, ValueError, json.JSONDecodeError):
        deleted = set()
    try:
        pool = {int(item) for item in json.loads(settings.get("admin_loader_rotation_pool") or "[]")}
    except (TypeError, ValueError, json.JSONDecodeError):
        pool = set()

    # Sultan Royal Badge is the durable fallback and can never be removed.
    deleted &= LOADER_DELETABLE_DESIGN_IDS
    available = [design_id for design_id in LOADER_DESIGN_IDS if design_id not in deleted]
    requested_active = str(settings.get("admin_loader_design") or "").strip()
    active = int(requested_active) if requested_active.isdigit() else LOADER_LOCKED_DESIGN_ID
    if active not in available:
        active = LOADER_LOCKED_DESIGN_ID
    pool = [design_id for design_id in LOADER_DESIGN_IDS if design_id in pool and design_id in available]

    return {
        "active": active,
        "available": available,
        "deleted": sorted(deleted),
        "pool": pool,
    }


@admin_bp.route("/loading-animations")
@permission_required("settings")
def loading_animations():
    """Manage the global loader without coupling it to the general Settings form."""
    settings = get_settings()
    config = _normalise_loader_configuration(settings)
    # Retire the legacy 42-design catalog the first time its settings page is opened.
    # Settings remain key/value records, so this is compatible with existing databases.
    normalised_values = {
        "admin_loader_design": str(config["active"]),
        "admin_loader_rotation_pool": json.dumps(config["pool"]),
        "admin_loader_deleted_designs": json.dumps(config["deleted"]),
    }
    if any(settings.get(key) != value for key, value in normalised_values.items()):
        for key, value in normalised_values.items():
            setting = db.session.get(Setting, key) or Setting(key=key)
            setting.value = value
            db.session.add(setting)
        db.session.commit()
    return render_template(
        "admin/loading_animations.html",
        available_designs=config["available"],
        active_design=str(config["active"]),
        rotation_enabled=settings.get("admin_loader_rotation_enabled") == "on",
        rotation_pool=config["pool"],
        deleted_designs=config["deleted"],
    )


@admin_bp.route("/loading-animations/autosave", methods=["POST"])
@permission_required("settings")
def autosave_loading_animations():
    """Persist loader selection, rotation pool and removals atomically."""
    payload = request.get_json(silent=True) or request.form
    try:
        deleted = sorted({int(item) for item in payload.get("deleted_designs", [])}) if isinstance(payload.get("deleted_designs"), list) else sorted({int(item) for item in json.loads(payload.get("deleted_designs", "[]"))})
        pool = sorted({int(item) for item in payload.get("rotation_pool", [])}) if isinstance(payload.get("rotation_pool"), list) else sorted({int(item) for item in json.loads(payload.get("rotation_pool", "[]"))})
    except (TypeError, ValueError, json.JSONDecodeError):
        return jsonify({"success": False, "message": "Invalid loading animation selection."}), 400

    valid = set(LOADER_DESIGN_IDS)
    if not set(deleted).issubset(valid) or not set(pool).issubset(valid):
        return jsonify({"success": False, "message": "Invalid loading animation design."}), 400

    if LOADER_LOCKED_DESIGN_ID in deleted:
        return jsonify({"success": False, "message": "Sultan Royal Badge is the protected default and cannot be removed."}), 400

    deleted = sorted(set(deleted) & LOADER_DELETABLE_DESIGN_IDS)
    available = valid - set(deleted)
    if not available:
        return jsonify({"success": False, "message": "At least one loading animation must remain available."}), 400

    rotation_enabled = str(payload.get("rotation_enabled", "off")) == "on"
    pool = [design_id for design_id in pool if design_id in available]
    requested_active = str(payload.get("active_design", "")).strip()
    active_design = int(requested_active) if requested_active.isdigit() else LOADER_LOCKED_DESIGN_ID
    if active_design not in available:
        active_design = LOADER_LOCKED_DESIGN_ID
    if rotation_enabled and not pool:
        pool = [active_design]

    values = {
        "admin_loader_design": str(active_design),
        "admin_loader_rotation_enabled": "on" if rotation_enabled else "off",
        "admin_loader_rotation_pool": json.dumps(pool),
        "admin_loader_deleted_designs": json.dumps(deleted),
    }
    for key, value in values.items():
        setting = db.session.get(Setting, key) or Setting(key=key)
        setting.value = value
        db.session.add(setting)
    audit("Loading Animation Settings", "Updated global loading animation configuration")
    db.session.commit()
    return jsonify({"success": True, "message": "Loading animation saved.", "config": values})




def _result_success_overlay_setting(key, value):
    setting = db.session.get(Setting, key) or Setting(key=key)
    setting.value = str(value)
    db.session.add(setting)
    return setting


def _non_final_overlay_copy_is_safe(template_key, title, subtitle):
    if template_key == "final":
        return True
    copy = f"{title} {subtitle}".casefold()
    return not any(marker in copy for marker in PROMOTION_COPY_MARKERS)


@admin_bp.route("/result-success-overlay")
@permission_required("results")
def result_success_overlay_settings():
    """Manage persistent result-success overlay copy and behavior."""
    settings = get_settings()
    overlay_settings = get_result_success_overlay_settings(settings)
    templates = []
    for template_key in RESULT_SUCCESS_TEMPLATE_ORDER:
        defaults = RESULT_SUCCESS_TEMPLATE_DEFAULTS[template_key]
        copy = result_success_template_copy(template_key, settings)
        templates.append({
            "key": template_key,
            "name": defaults["name"],
            "title": copy["title"],
            "subtitle": copy["subtitle"],
            "title_override": str(settings.get(f"result_success_overlay_{template_key}_title") or "").strip(),
            "subtitle_override": str(settings.get(f"result_success_overlay_{template_key}_subtitle") or "").strip(),
            "is_active": overlay_settings["active_template"] == template_key,
        })
    return render_template(
        "admin/result_success_overlay_settings.html",
        templates=templates,
        overlay_settings=overlay_settings,
        template_defaults=RESULT_SUCCESS_TEMPLATE_DEFAULTS,
        shared_labels=result_success_overlay_labels(settings),
        shared_label_defaults=RESULT_SUCCESS_OVERLAY_LABEL_DEFAULTS,
        preview_tiers={str(position): result_success_position_tier(position, settings) for position in range(1, 22)},
    )


@admin_bp.route("/result-success-overlay/template/<string:template_key>", methods=["POST"])
@permission_required("results")
def update_result_success_overlay_template(template_key):
    if template_key not in RESULT_SUCCESS_TEMPLATE_DEFAULTS:
        return _result_success_overlay_response(False, "Unknown exam template.", 404)

    title = (request.form.get("title") or "").strip()
    subtitle = (request.form.get("subtitle") or "").strip()
    if len(title) > 180 or len(subtitle) > 280:
        return _result_success_overlay_response(False, "Overlay title or subtitle is too long.", 400)
    if not _non_final_overlay_copy_is_safe(template_key, title, subtitle):
        return _result_success_overlay_response(
            False,
            "Promotion wording is reserved for the Final exam template.",
            400,
        )

    settings = get_settings()
    old_title = str(settings.get(f"result_success_overlay_{template_key}_title") or "")
    old_subtitle = str(settings.get(f"result_success_overlay_{template_key}_subtitle") or "")
    _result_success_overlay_setting(f"result_success_overlay_{template_key}_title", title)
    _result_success_overlay_setting(f"result_success_overlay_{template_key}_subtitle", subtitle)
    audit(
        "Result Success Overlay Copy Updated",
        f"Template {template_key}: title {old_title!r} -> {title!r}; subtitle {old_subtitle!r} -> {subtitle!r}",
    )
    db.session.commit()
    resolved_copy = result_success_template_copy(template_key, get_settings())
    return _result_success_overlay_response(True, "Template copy saved.", template_key=template_key, copy=resolved_copy)


@admin_bp.route("/result-success-overlay/labels", methods=["POST"])
@permission_required("results")
def update_result_success_overlay_labels():
    settings = get_settings()
    changes = []
    for key, default in RESULT_SUCCESS_OVERLAY_LABEL_DEFAULTS.items():
        value = (request.form.get(key) or "").strip()
        if len(value) > 90:
            return _result_success_overlay_response(False, "Shared overlay labels must be 90 characters or fewer.", 400)
        resolved_value = value or default
        setting_key = f"result_success_overlay_label_{key}"
        previous = str(settings.get(setting_key) or "")
        if previous != resolved_value:
            _result_success_overlay_setting(setting_key, resolved_value)
            changes.append(f"{key}: {previous!r} -> {resolved_value!r}")
    if changes:
        audit("Result Success Overlay Labels Updated", "; ".join(changes))
        db.session.commit()
    return _result_success_overlay_response(
        True,
        "Shared overlay labels saved.",
        labels=result_success_overlay_labels(get_settings()),
    )


@admin_bp.route("/result-success-overlay/activate", methods=["POST"])
@permission_required("results")
def activate_result_success_overlay_template():
    template_key = (request.form.get("template_key") or "").strip().casefold()
    if template_key not in RESULT_SUCCESS_TEMPLATE_DEFAULTS:
        return _result_success_overlay_response(False, "Choose a valid exam template.", 400)

    settings = get_settings()
    old_template = str(settings.get("result_success_overlay_active_template") or "m1")
    _result_success_overlay_setting("result_success_overlay_active_template", template_key)
    audit(
        "Result Success Overlay Template Activated",
        f"Active template {old_template!r} -> {template_key!r}",
    )
    db.session.commit()
    return _result_success_overlay_response(True, "Active exam template updated.", active_template=template_key)


@admin_bp.route("/result-success-overlay/timing", methods=["POST"])
@permission_required("results")
def update_result_success_overlay_timing():
    try:
        duration = int(request.form.get("duration_seconds", 8))
    except (TypeError, ValueError):
        return _result_success_overlay_response(False, "Overlay duration must be a whole number.", 400)
    if not 3 <= duration <= 60:
        return _result_success_overlay_response(False, "Overlay duration must be between 3 and 60 seconds.", 400)

    show_progress = "on" if request.form.get("show_progress_bar") in {"on", "true", "1"} else "off"
    allow_close = "on" if request.form.get("allow_manual_close") in {"on", "true", "1"} else "off"
    settings = get_settings()
    previous = get_result_success_overlay_settings(settings)
    _result_success_overlay_setting("result_success_overlay_duration_seconds", duration)
    _result_success_overlay_setting("result_success_overlay_show_progress_bar", show_progress)
    _result_success_overlay_setting("result_success_overlay_allow_manual_close", allow_close)
    audit(
        "Result Success Overlay Timing Updated",
        "Timing "
        f"{previous['duration_seconds']}s -> {duration}s; progress {previous['show_progress_bar']} -> {show_progress}; "
        f"manual close {previous['allow_manual_close']} -> {allow_close}",
    )
    db.session.commit()
    return _result_success_overlay_response(
        True,
        "Overlay timing saved.",
        settings=get_result_success_overlay_settings(get_settings()),
    )


@admin_bp.route("/settings/grade-scale/<int:grade_id>/delete", methods=["POST"])
@permission_required("settings")
def delete_grade_scale(grade_id):
    # Redirect to Results Hub Grade Management (consolidated grade scale operations)
    flash("Grade Scale Management has been moved to Results → Grade Management", "info")
    return redirect(url_for("admin_advanced_results.grade_management"))


@admin_bp.route("/audit-logs")
def audit_logs():
    rows = AuditLog.query.order_by(AuditLog.created_at.desc()).limit(500).all()
    return render_template("admin/audit_logs.html", rows=rows)


@admin_bp.route("/classes/<int:row_id>/delete", methods=["POST"])
def delete_class(row_id):
    return delete_row(SchoolClass, row_id, "admin_advanced_results.new_dashboard")


@admin_bp.route("/subjects/<int:row_id>/delete", methods=["POST"])
def delete_subject(row_id):
    return delete_row(Subject, row_id, "admin_advanced_results.new_setup")


@admin_bp.route("/academic-years/<int:row_id>/delete", methods=["POST"])
def delete_academic_year(row_id):
    return delete_row(AcademicYear, row_id, "admin_advanced_results.new_setup")


def simple_crud(model, template, fields):
    if request.method == "POST":
        row = db.session.get(model, int(request.form.get("id") or 0)) or model()
        for field in fields:
            setattr(row, field, request.form.get(field, "").strip())
        db.session.add(row)
        db.session.commit()
        flash("Saved successfully.", "success")
        return redirect(url_for(f"admin.{template}"))
    return render_template(f"admin/{template}.html", rows=model.query.order_by(model.id.desc()).all())


def delete_row(model, row_id, endpoint):
    row = db.session.get(model, row_id) or abort_404()
    db.session.delete(row)
    audit("Admin Updates", f"Deleted {model.__name__} {row_id}")
    db.session.commit()
    flash("Deleted successfully.", "success")
    return redirect(url_for(endpoint))


def get_or_create(model, **kwargs):
    row = model.query.filter_by(**kwargs).first()
    if row:
        return row
    row = model(**kwargs)
    db.session.add(row)
    db.session.flush()
    return row


def workbook_response(workbook, filename):
    from tempfile import NamedTemporaryFile

    tmp = NamedTemporaryFile(delete=False, suffix=".xlsx")
    workbook.save(tmp.name)
    tmp.close()
    return send_file(tmp.name, as_attachment=True, download_name=filename, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


def current_app_config(key):
    from flask import current_app

    return current_app.config[key]


def abort_404():
    from flask import abort

    abort(404)


# =========================
# INVIGILATOR MANAGEMENT
# =========================

@admin_bp.route("/invigilators")
def invigilators():
    """Manage Exam Invigilators"""
    q = request.args.get("q", "").strip()
    status_filter = request.args.get("status", "")
    role_filter = request.args.get("role", "")
    school_filter = request.args.get("school", "")
    validity_filter = request.args.get("validity", "")

    query = ExamInvigilator.query

    if q:
        query = query.filter(
            or_(
                ExamInvigilator.full_name.like(f"%{q}%"),
                ExamInvigilator.username.like(f"%{q}%"),
                ExamInvigilator.invigilator_id.like(f"%{q}%"),
                ExamInvigilator.mobile_number.like(f"%{q}%")
            )
        )

    if status_filter:
        query = query.filter(ExamInvigilator.status == status_filter)

    if role_filter:
        query = query.filter(ExamInvigilator.role == role_filter)

    if school_filter:
        query = query.filter(ExamInvigilator.school.like(f"%{school_filter}%"))

    if validity_filter:
        today = date.today()
        if validity_filter == "active":
            query = query.filter(ExamInvigilator.active_from <= today, ExamInvigilator.active_until >= today)
        elif validity_filter == "expiring_soon":
            query = query.filter(ExamInvigilator.active_until.between(today, today + timedelta(days=7)))
        elif validity_filter == "expired":
            query = query.filter(ExamInvigilator.active_until < today)

    invigilators = query.order_by(ExamInvigilator.created_at.desc()).all()

    # Statistics
    today = date.today()
    week_from_now = today + timedelta(days=7)

    stats = {
        "total": ExamInvigilator.query.count(),
        "active": ExamInvigilator.query.filter_by(status="Active").count(),
        "inactive": ExamInvigilator.query.filter_by(status="Inactive").count(),
        "expiring_soon": ExamInvigilator.query.filter(
            ExamInvigilator.active_until.between(today, week_from_now)
        ).count(),
        "supervisors": ExamInvigilator.query.filter(ExamInvigilator.role.in_(["Supervisor", "Chief Invigilator"])).count(),
        "administrators": ExamInvigilator.query.filter_by(role="Administrator").count(),
    }

    return render_template(
        "admin/invigilators.html",
        invigilators=invigilators,
        stats=stats,
        q=q,
        status_filter=status_filter,
        role_filter=role_filter,
        school_filter=school_filter,
        validity_filter=validity_filter
    )


@admin_bp.route("/invigilators/add", methods=["GET", "POST"])
def invigilator_add():
    """Add new invigilator"""
    if request.method == "POST":
        invigilator_id = request.form.get("invigilator_id", "").strip()
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        full_name = request.form.get("full_name", "").strip()
        mobile_number = request.form.get("mobile_number", "").strip()
        role = request.form.get("role", "Invigilator")
        school = request.form.get("school", "").strip()
        notes = request.form.get("notes", "").strip()
        active_from = request.form.get("active_from", "")
        active_until = request.form.get("active_until", "")

        # Validate required fields
        if not all([invigilator_id, username, password, full_name]):
            flash("Invigilator ID, username, password, and full name are required.", "danger")
            return render_template("admin/invigilator_form.html", invigilator=None)
        is_valid_password, password_error = validate_invigilator_password(password)
        if not is_valid_password:
            flash(password_error, "danger")
            return render_template("admin/invigilator_form.html", invigilator=None)

        # Check if invigilator_id or username already exists
        if ExamInvigilator.query.filter_by(invigilator_id=invigilator_id).first():
            flash("Invigilator ID already exists.", "danger")
            return render_template("admin/invigilator_form.html", invigilator=None)

        if ExamInvigilator.query.filter_by(username=username).first():
            flash("Username already exists.", "danger")
            return render_template("admin/invigilator_form.html", invigilator=None)

        # Create invigilator
        invigilator = ExamInvigilator(
            invigilator_id=invigilator_id,
            username=username,
            full_name=full_name,
            mobile_number=mobile_number,
            role=role,
            school=school,
            notes=notes,
            active_from=datetime.strptime(active_from, "%Y-%m-%d").date() if active_from else None,
            active_until=datetime.strptime(active_until, "%Y-%m-%d").date() if active_until else None
        )
        invigilator.set_password(password)
        invigilator.visible_password = password

        db.session.add(invigilator)
        db.session.commit()

        audit("Invigilator Created", f"Created invigilator {invigilator.full_name} ({invigilator.invigilator_id})")
        flash(f"Invigilator {full_name} added successfully!", "success")
        return redirect(url_for("admin.invigilators"))

    return render_template("admin/invigilator_form.html", invigilator=None)


@admin_bp.route("/invigilators/<int:invigilator_id>/edit", methods=["GET", "POST"])
def invigilator_edit(invigilator_id):
    """Edit invigilator"""
    invigilator = ExamInvigilator.query.get_or_404(invigilator_id)

    if request.method == "POST":
        invigilator.invigilator_id = request.form.get("invigilator_id", "").strip()
        invigilator.username = request.form.get("username", "").strip()
        invigilator.full_name = request.form.get("full_name", "").strip()
        invigilator.mobile_number = request.form.get("mobile_number", "").strip()
        invigilator.role = request.form.get("role", "Invigilator")
        invigilator.school = request.form.get("school", "").strip()
        invigilator.notes = request.form.get("notes", "").strip()
        invigilator.status = request.form.get("status", "Active")
        invigilator.active_from = datetime.strptime(request.form.get("active_from", ""), "%Y-%m-%d").date() if request.form.get("active_from") else None
        invigilator.active_until = datetime.strptime(request.form.get("active_until", ""), "%Y-%m-%d").date() if request.form.get("active_until") else None

        # Update password if provided
        new_password = request.form.get("new_password", "")
        if new_password:
            is_valid_password, password_error = validate_invigilator_password(new_password)
            if not is_valid_password:
                flash(password_error, "danger")
                return render_template("admin/invigilator_form.html", invigilator=invigilator)
            invigilator.set_password(new_password)
            invigilator.visible_password = new_password

        # Handle photo upload
        if request.files.get("photo") and request.files["photo"].filename:
            photo = request.files["photo"]
            if allowed_file(photo.filename, ALLOWED_PHOTOS):
                photo_url = upload_image(photo, "invigilators")
                invigilator.photo_path = photo_url

        db.session.commit()

        audit("Invigilator Updated", f"Updated invigilator {invigilator.full_name} ({invigilator.invigilator_id})")
        flash(f"Invigilator {invigilator.full_name} updated successfully!", "success")
        return redirect(url_for("admin.invigilators"))

    return render_template("admin/invigilator_form.html", invigilator=invigilator)


@admin_bp.route("/invigilators/<int:invigilator_id>/delete", methods=["POST"])
def invigilator_delete(invigilator_id):
    """Delete invigilator"""
    invigilator = ExamInvigilator.query.get_or_404(invigilator_id)

    full_name = invigilator.full_name
    invigilator_id_str = invigilator.invigilator_id

    db.session.delete(invigilator)
    db.session.commit()

    audit("Invigilator Deleted", f"Deleted invigilator {full_name} ({invigilator_id_str})")
    flash(f"Invigilator {full_name} deleted successfully!", "success")
    return redirect(url_for("admin.invigilators"))


@admin_bp.route("/invigilators/<int:invigilator_id>/toggle-status", methods=["POST"])
def invigilator_toggle_status(invigilator_id):
    """Toggle invigilator active status"""
    invigilator = ExamInvigilator.query.get_or_404(invigilator_id)

    invigilator.is_active = not invigilator.is_active
    if not invigilator.is_active:
        invigilator.status = "Inactive"
    else:
        invigilator.status = "Active"

    db.session.commit()

    status_text = "activated" if invigilator.is_active else "deactivated"
    audit("Invigilator Status Changed", f"{status_text.capitalize()} invigilator {invigilator.full_name}")
    flash(f"Invigilator {invigilator.full_name} {status_text} successfully!", "success")
    return redirect(url_for("admin.invigilators"))


@admin_bp.route("/invigilators/<int:invigilator_id>/reset-password", methods=["POST"])
def invigilator_reset_password(invigilator_id):
    """Reset invigilator password and force change"""
    invigilator = ExamInvigilator.query.get_or_404(invigilator_id)

    temp_password = generate_invigilator_password()
    invigilator.set_password(temp_password)
    invigilator.visible_password = temp_password
    invigilator.force_password_change = True
    db.session.commit()

    audit("Invigilator Password Reset", f"Reset password for {invigilator.full_name}")
    flash(f"Temporary password for {invigilator.full_name}: {temp_password}", "info")
    return redirect(url_for("admin.invigilators"))


@admin_bp.route("/invigilators/<int:invigilator_id>/history")
def invigilator_history(invigilator_id):
    """View invigilator login history"""
    invigilator = ExamInvigilator.query.get_or_404(invigilator_id)
    history = InvigilatorLoginHistory.query.filter_by(invigilator_id=invigilator_id).order_by(InvigilatorLoginHistory.login_time.desc()).limit(50).all()

    return render_template("admin/invigilator_history.html", invigilator=invigilator, history=history)


# =========================
# INCIDENT REPORT SETTINGS
# =========================

@admin_bp.route("/incident-settings")
def incident_settings():
    """Manage Incident Report Settings"""
    ensure_incident_setting_defaults()
    settings = IncidentReportSettings.query.order_by(IncidentReportSettings.category, IncidentReportSettings.setting_key).all()
    categories = IncidentCategory.query.order_by(IncidentCategory.sort_order, IncidentCategory.name).all()
    severities = SeverityLevel.query.order_by(SeverityLevel.sort_order, SeverityLevel.name).all()
    actions = IncidentAction.query.order_by(IncidentAction.sort_order, IncidentAction.name).all()

    # Group settings by category
    grouped_settings = {}
    for setting in settings:
        if setting.category not in grouped_settings:
            grouped_settings[setting.category] = []
        grouped_settings[setting.category].append(setting)

    return render_template(
        "admin/incident_settings.html",
        grouped_settings=grouped_settings,
        categories=categories,
        severities=severities,
        actions=actions,
    )


@admin_bp.route("/incident-settings/update", methods=["POST"])
def incident_settings_update():
    """Update incident report settings"""
    ensure_incident_setting_defaults()
    # Get all settings first to handle unchecked checkboxes
    all_settings = IncidentReportSettings.query.all()

    for setting in all_settings:
        form_key = f"setting_{setting.setting_key}"

        if setting.setting_type == 'boolean':
            # Checkboxes only send 'on' when checked, so we need to handle unchecked case
            if form_key in request.form:
                setting.setting_value = 'true'
            else:
                setting.setting_value = 'false'
        else:
            # For non-boolean settings, use the form value if present
            if form_key in request.form:
                value = request.form[form_key]
                if setting.setting_key == "incident_reference_prefix":
                    value = "".join(ch for ch in value.strip().upper() if ch.isalnum())[:10] or "INC"
                setting.setting_value = value

    # Handle footer logo upload
    upload = request.files.get("setting_footer_logo_path")
    if upload and upload.filename:
        if allowed_file(upload.filename, ALLOWED_PHOTOS):
            image_url = upload_image(upload, "school/settings")
            setting = IncidentReportSettings.query.filter_by(setting_key="footer_logo_path").first()
            if setting:
                setting.setting_value = image_url

    db.session.commit()

    audit("Incident Settings Updated", "Updated incident report form settings")
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({"status": "success", "message": "Incident settings saved."})
    flash("Incident report settings updated successfully!", "success")
    return redirect(url_for("admin.incident_settings"))


def incident_lookup_model(kind):
    models = {
        "category": IncidentCategory,
        "severity": SeverityLevel,
        "action": IncidentAction,
    }
    return models.get(kind)


def incident_lookup_redirect():
    return redirect(url_for("admin.incident_settings", _anchor="incident-management"))


def incident_lookup_response(message, category="success", status=200):
    """Serve JSON to the enhanced settings UI and keep the classic redirect workflow."""
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify(success=category == "success", message=message), status
    flash(message, category)
    return incident_lookup_redirect()


def safe_lookup_sort_order(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def normalize_lookup_color(value):
    color = (value or "").strip()
    if len(color) == 7 and color.startswith("#") and all(ch in "0123456789abcdefABCDEF" for ch in color[1:]):
        return color
    return "#64748b"


def lookup_name_conflict(model, name, row_id=None):
    """Return an existing lookup with the same label, ignoring the edited row."""
    query = model.query.filter(model.name.ilike(name))
    if row_id is not None:
        query = query.filter(model.id != row_id)
    return query.first()


def record_incident_lookup_audit(action, details):
    """Auditing must not turn a successful lookup write into a failed user action."""
    try:
        audit(action, details)
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Incident lookup audit could not be saved")


@admin_bp.route("/incident-settings/<kind>/create", methods=["POST"])
def incident_lookup_create(kind):
    model = incident_lookup_model(kind)
    if not model:
        abort(404)
    name = request.form.get("name", "").strip()
    if not name:
        return incident_lookup_response("Name is required.", "danger", 400)
    if lookup_name_conflict(model, name):
        return incident_lookup_response(f"A {kind} named '{name}' already exists.", "danger", 409)
    row = model(name=name)
    if hasattr(row, "description") and "description" in request.form:
        row.description = request.form.get("description", "").strip()
    if hasattr(row, "color"):
        row.color = normalize_lookup_color(request.form.get("color"))
    row.sort_order = safe_lookup_sort_order(request.form.get("sort_order"))
    row.is_active = request.form.get("is_active") in ("on", "true", "1")
    db.session.add(row)
    try:
        db.session.commit()
        record_incident_lookup_audit("Incident Settings Updated", f"Created incident {kind}: {name}")
        return incident_lookup_response(f"{kind.title()} created successfully.")
    except IntegrityError:
        db.session.rollback()
        current_app.logger.warning("Incident %s create rejected because the name already exists: %s", kind, name)
        return incident_lookup_response(f"Unable to save {kind}: that name already exists.", "danger", 409)
    except SQLAlchemyError as exc:
        db.session.rollback()
        current_app.logger.exception("Incident %s create failed: %s", kind, exc)
        return incident_lookup_response(f"Unable to save {kind}. Please try again.", "danger", 500)


@admin_bp.route("/incident-settings/<kind>/<int:row_id>/update", methods=["POST"])
def incident_lookup_update(kind, row_id):
    model = incident_lookup_model(kind)
    if not model:
        abort(404)
    row = model.query.get_or_404(row_id)
    name = request.form.get("name", "").strip()
    if not name:
        return incident_lookup_response("Name is required.", "danger", 400)
    if lookup_name_conflict(model, name, row.id):
        return incident_lookup_response(f"Unable to save {kind}: a record named '{name}' already exists.", "danger", 409)
    row.name = name
    if hasattr(row, "description") and "description" in request.form:
        row.description = request.form.get("description", "").strip()
    if hasattr(row, "color"):
        row.color = normalize_lookup_color(request.form.get("color"))
    row.sort_order = safe_lookup_sort_order(request.form.get("sort_order"))
    row.is_active = request.form.get("is_active") in ("on", "true", "1")
    db.session.add(row)
    try:
        db.session.commit()
        record_incident_lookup_audit("Incident Settings Updated", f"Updated incident {kind}: {name}")
        return incident_lookup_response(f"{kind.title()} saved successfully.")
    except IntegrityError:
        db.session.rollback()
        current_app.logger.warning("Incident %s update rejected because the name already exists: %s", kind, name)
        return incident_lookup_response(f"Unable to save {kind}: that name already exists.", "danger", 409)
    except SQLAlchemyError as exc:
        db.session.rollback()
        current_app.logger.exception("Incident %s update failed for row %s: %s", kind, row_id, exc)
        return incident_lookup_response(f"Unable to save {kind}. Please try again.", "danger", 500)


@admin_bp.route("/incident-settings/<kind>/<int:row_id>/delete", methods=["POST"])
def incident_lookup_delete(kind, row_id):
    model = incident_lookup_model(kind)
    if not model:
        abort(404)
    row = model.query.get_or_404(row_id)
    name = row.name
    referenced = False
    if kind == "category":
        referenced = IncidentReport.query.filter_by(category_id=row.id).first() is not None
    elif kind == "severity":
        referenced = IncidentReport.query.filter_by(severity_id=row.id).first() is not None

    try:
        if referenced:
            row.is_active = False
            db.session.add(row)
            message = f"{kind.title()} is used by existing reports, so it was deactivated safely."
        else:
            db.session.delete(row)
            message = f"{kind.title()} deleted successfully."
        db.session.commit()
        record_incident_lookup_audit("Incident Settings Updated", f"Deleted/deactivated incident {kind}: {name}")
        return incident_lookup_response(message)
    except SQLAlchemyError as exc:
        db.session.rollback()
        current_app.logger.exception("Incident %s delete failed for row %s: %s", kind, row_id, exc)
        return incident_lookup_response(f"Unable to delete {kind}. It was not changed.", "danger", 500)


CONFIG_CENTER_SECTIONS = {
    'academic-years': {
        'title': 'Academic Years',
        'description': 'Manage academic calendar years and their status',
        'columns': ['Academic Year', 'Status', 'Type', 'Start Date', 'End Date', 'Created By']
    },
    'exam-types': {
        'title': 'Exam Types',
        'description': 'Manage examination types and their configurations',
        'columns': ['Exam Type', 'Status', 'Code', 'Weight %', 'Academic Year', 'Created By']
    },
    'levels': {
        'title': 'Levels & Classes',
        'description': 'Manage academic levels, classes, and sections as one hierarchy',
        'columns': ['Level / Class', 'Status', 'Type', 'Students', 'Sections', 'Created By']
    },
    'subjects': {
        'title': 'Subjects',
        'description': 'Manage subject offerings and their level assignments',
        'columns': ['Subject', 'Status', 'Level', 'Classes', 'Created By']
    },
    'audit-logs': {
        'title': 'Audit Logs',
        'description': 'View all system configuration changes',
        'columns': ['Date/Time', 'Action', 'User', 'Module', 'Details', 'IP Address']
    },
    'archive': {
        'title': 'Archive',
        'description': 'View and restore safely archived configuration items',
        'columns': ['Item', 'Type', 'Archived Date', 'Archived By', 'Reason']
    }
}


CONFIG_CENTER_MODEL_MAP = {
    'academic-years': AcademicYear,
    'exam-types': Exam,
    'levels': AcademicLevel,
    'classes': AcademicClass,
    'subjects': Subject,
}

CONFIG_CENTER_MUTABLE_TYPES = {'academic-years', 'exam-types', 'levels', 'classes', 'subjects'}


def _config_center_auth_valid():
    if not session.get('config_center_authenticated'):
        return False
    expiry = session.get('config_center_auth_expiry')
    if expiry:
        try:
            expiry_dt = datetime.fromisoformat(expiry)
        except (TypeError, ValueError):
            return False
        if datetime.utcnow() > expiry_dt:
            session.pop('config_center_authenticated', None)
            session.pop('config_center_auth_time', None)
            session.pop('config_center_auth_expiry', None)
            return False
    return True


def config_center_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if _config_center_auth_valid():
            return fn(*args, **kwargs)
        if request.path.startswith('/admin/config-center/api') or request.path.endswith('/confirm-password'):
            return jsonify({'success': False, 'message': 'Configuration Center authentication is required.'}), 403
        flash('Please authenticate from Results Hub Setup before opening Configuration Center.', 'warning')
        return redirect(url_for('admin_advanced_results.new_setup'))
    return wrapper


def _item_display_name(item):
    return getattr(item, 'name', None) or getattr(item, 'key', None) or str(getattr(item, 'id', 'item'))


def _config_item_is_active(item_type, item):
    if item_type == 'academic-years':
        return bool(getattr(item, 'is_current', False))
    if hasattr(item, 'is_active'):
        return bool(item.is_active)
    return bool(getattr(item, 'status', 'active') == 'active')


def _config_dependencies(item_type, item_id):
    dependencies = []
    if item_type == 'academic-years':
        exam_count = Exam.query.filter_by(academic_year_id=item_id).count()
        student_count = Student.query.filter_by(academic_year_id=item_id).count()
        attendance_count = AttendanceRecord.query.filter_by(academic_year_id=item_id).count()
        if exam_count:
            dependencies.append(f'Exam Types: {exam_count}')
        if student_count:
            dependencies.append(f'Students: {student_count}')
        if attendance_count:
            dependencies.append(f'Attendance Records: {attendance_count}')
    elif item_type == 'exam-types':
        result_count = Result.query.filter_by(exam_id=item_id).count()
        verification_count = ReportVerification.query.filter_by(exam_id=item_id).count()
        incident_count = IncidentReport.query.filter_by(exam_id=item_id).count()
        grade_count = GradeScale.query.filter_by(exam_id=item_id).count()
        if result_count:
            dependencies.append(f'Results: {result_count}')
        if verification_count:
            dependencies.append(f'Report Verifications: {verification_count}')
        if incident_count:
            dependencies.append(f'Incident Reports: {incident_count}')
        if grade_count:
            dependencies.append(f'Grade Scales: {grade_count}')
    elif item_type == 'levels':
        class_count = AcademicClass.query.filter_by(academic_level_id=item_id).count()
        student_count = Student.query.filter_by(academic_level_id=item_id).count()
        subject_count = Subject.query.filter_by(academic_level_id=item_id).count()
        if class_count:
            dependencies.append(f'Classes: {class_count}')
        if student_count:
            dependencies.append(f'Students: {student_count}')
        if subject_count:
            dependencies.append(f'Subjects: {subject_count}')
    elif item_type == 'classes':
        section_count = AcademicSection.query.filter_by(academic_class_id=item_id).count()
        student_count = Student.query.filter_by(academic_class_id=item_id).count()
        exam_count = Exam.query.filter_by(academic_class_id=item_id).count()
        if section_count:
            dependencies.append(f'Sections: {section_count}')
        if student_count:
            dependencies.append(f'Students: {student_count}')
        if exam_count:
            dependencies.append(f'Exams: {exam_count}')
    elif item_type == 'subjects':
        result_count = Result.query.filter_by(subject_id=item_id).count()
        incident_count = IncidentReport.query.filter_by(subject_id=item_id).count()
        if result_count:
            dependencies.append(f'Results: {result_count}')
        if incident_count:
            dependencies.append(f'Incident Reports: {incident_count}')
    return dependencies


def _config_model(item_type):
    return CONFIG_CENTER_MODEL_MAP.get(item_type)


def _cascade_delete_config_item(item_type, item_id):
    """Recursively delete dependent records for force delete override"""
    if item_type == 'academic-years':
        # AttendanceRecords
        AttendanceRecord.query.filter_by(academic_year_id=item_id).delete()
        # IdCardIssues
        IdCardIssue.query.filter_by(academic_year_id=item_id).delete()
        # Exams and their dependencies (Result, ReportVerification, IncidentReport, GradeScale)
        exams = Exam.query.filter_by(academic_year_id=item_id).all()
        for exam in exams:
            _cascade_delete_config_item('exam-types', exam.id)
            db.session.delete(exam)
        # Students and their dependencies (Results, etc.)
        students = Student.query.filter_by(academic_year_id=item_id).all()
        for student in students:
            Result.query.filter_by(student_id=student.id).delete()
            AttendanceRecord.query.filter_by(student_id=student.id).delete()
            IdCardIssue.query.filter_by(student_id=student.id).delete()
            IncidentReport.query.filter_by(student_id=student.id).delete()
            db.session.delete(student)

    elif item_type == 'exam-types':
        Result.query.filter_by(exam_id=item_id).delete()
        ReportVerification.query.filter_by(exam_id=item_id).delete()
        IncidentReport.query.filter_by(exam_id=item_id).delete()
        GradeScale.query.filter_by(exam_id=item_id).delete()

    elif item_type == 'levels':
        # Classes
        classes = AcademicClass.query.filter_by(academic_level_id=item_id).all()
        for cls in classes:
            _cascade_delete_config_item('classes', cls.id)
            db.session.delete(cls)
        # Subjects
        subjects = Subject.query.filter_by(academic_level_id=item_id).all()
        for subj in subjects:
            _cascade_delete_config_item('subjects', subj.id)
            db.session.delete(subj)
        # Students
        students = Student.query.filter_by(academic_level_id=item_id).all()
        for student in students:
            Result.query.filter_by(student_id=student.id).delete()
            AttendanceRecord.query.filter_by(student_id=student.id).delete()
            IdCardIssue.query.filter_by(student_id=student.id).delete()
            IncidentReport.query.filter_by(student_id=student.id).delete()
            db.session.delete(student)

    elif item_type == 'classes':
        # Sections
        AcademicSection.query.filter_by(academic_class_id=item_id).delete()
        # Students
        students = Student.query.filter_by(academic_class_id=item_id).all()
        for student in students:
            Result.query.filter_by(student_id=student.id).delete()
            AttendanceRecord.query.filter_by(student_id=student.id).delete()
            IdCardIssue.query.filter_by(student_id=student.id).delete()
            IncidentReport.query.filter_by(student_id=student.id).delete()
            db.session.delete(student)
        # Exams
        exams = Exam.query.filter_by(academic_class_id=item_id).all()
        for exam in exams:
            _cascade_delete_config_item('exam-types', exam.id)
            db.session.delete(exam)

    elif item_type == 'subjects':
        Result.query.filter_by(subject_id=item_id).delete()
        IncidentReport.query.filter_by(subject_id=item_id).delete()


def _config_status(item_type, item):
    return 'active' if _config_item_is_active(item_type, item) else 'archived'


def _parse_int(value, default=None):
    if value in (None, ''):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_float(value, default=None):
    if value in (None, ''):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _duplicate_exists(model, filters, exclude_id=None):
    query = model.query.filter_by(**filters)
    if exclude_id:
        query = query.filter(model.id != exclude_id)
    return query.first() is not None


def _audit_config_change(action, item_type, item, old_value=None, new_value=None, reason=None):
    details = {
        'item_type': item_type,
        'item_id': getattr(item, 'id', None),
        'item_name': _item_display_name(item),
        'old_value': old_value,
        'new_value': new_value,
        'reason': reason,
        'device': request.headers.get('User-Agent', '')[:180],
    }
    audit(action, str(details))


@admin_bp.before_request
def enforce_config_center_access():
    if not request.path.startswith('/admin/config-center'):
        return None
    if request.endpoint == 'admin.config_center_authenticate':
        return None
    if _config_center_auth_valid():
        return None
    if request.path.startswith('/admin/config-center/api') or request.endpoint == 'admin.config_center_confirm_password':
        return jsonify({'success': False, 'message': 'Configuration Center authentication is required.'}), 403
    flash('Please authenticate from Results Hub Setup before opening Configuration Center.', 'warning')
    return redirect(url_for('admin_advanced_results.new_setup'))


# Configuration Center Routes
@admin_bp.route("/config-center")
@login_required
@config_center_required
def config_center():
    """Configuration Center Main Page"""
    section = request.args.get('section', 'academic-years')
    if section not in CONFIG_CENTER_SECTIONS or section == 'grade-system':
        section = 'academic-years'
    info = CONFIG_CENTER_SECTIONS.get(section, CONFIG_CENTER_SECTIONS['academic-years'])

    classes_by_level = {}
    class_student_counts = {}
    subject_class_map = {}
    subject_icons = {}
    selected_subject_year_id = _parse_int(request.args.get("year_id"))
    selected_subject_level_id = _parse_int(request.args.get("level_id"))
    settings = get_settings()
    if not selected_subject_year_id:
        latest_year = AcademicYear.query.order_by(AcademicYear.is_current.desc(), AcademicYear.name.desc(), AcademicYear.id.desc()).first()
        selected_subject_year_id = latest_year.id if latest_year else None

    # Get data based on section
    items = []
    if section == 'academic-years':
        items = AcademicYear.query.order_by(AcademicYear.name.desc()).all()
    elif section == 'exam-types':
        items = Exam.query.order_by(Exam.academic_year_id.desc(), Exam.sort_order, Exam.name).all()
    elif section == 'levels':
        items = AcademicLevel.query.order_by(AcademicLevel.sort_order, AcademicLevel.name).all()
        level_ids = [level.id for level in items]
        classes = AcademicClass.query.filter(AcademicClass.academic_level_id.in_(level_ids)).order_by(AcademicClass.sort_order, AcademicClass.name).all() if level_ids else []
        classes_by_level = {level.id: [] for level in items}
        for academic_class in classes:
            classes_by_level.setdefault(academic_class.academic_level_id, []).append(academic_class)
            class_student_counts[academic_class.id] = Student.query.filter_by(academic_class_id=academic_class.id).count()
    elif section == 'subjects':
        subject_query = Subject.query
        if selected_subject_level_id:
            subject_query = subject_query.filter_by(academic_level_id=selected_subject_level_id)
        else:
            subject_query = subject_query.filter(Subject.academic_level_id.is_(None))
        items = subject_query.order_by(Subject.academic_level_id, Subject.sort_order, Subject.name).all()
        for subject in items:
            subject_icons[subject.id] = subject_icon(subject.name, settings)
            if subject.academic_level_id:
                subject_class_map[subject.id] = AcademicClass.query.filter_by(academic_level_id=subject.academic_level_id, is_active=True).order_by(AcademicClass.sort_order, AcademicClass.name).all()
            else:
                subject_class_map[subject.id] = []
    elif section == 'audit-logs':
        items = AuditLog.query.order_by(AuditLog.created_at.desc()).limit(50).all()
    elif section == 'archive':
        items = []
        for item in AcademicYear.query.filter_by(is_current=False).order_by(AcademicYear.name.desc()).all():
            items.append({'id': item.id, 'item_type': 'academic-years', 'name': item.name, 'status': 'archived', 'archived_at': item.updated_at})
        for item in Exam.query.filter_by(is_active=False).order_by(Exam.updated_at.desc()).all():
            items.append({'id': item.id, 'item_type': 'exam-types', 'name': item.name, 'status': 'archived', 'archived_at': item.updated_at})
        for item in AcademicLevel.query.filter_by(is_active=False).order_by(AcademicLevel.sort_order, AcademicLevel.name).all():
            items.append({'id': item.id, 'item_type': 'levels', 'name': item.name, 'status': 'archived', 'archived_at': item.updated_at})
        for item in AcademicClass.query.filter_by(is_active=False).order_by(AcademicClass.sort_order, AcademicClass.name).all():
            items.append({'id': item.id, 'item_type': 'classes', 'name': item.name, 'status': 'archived', 'archived_at': item.updated_at})

    # Calculate counts
    total_count = len(items)
    if section == 'archive':
        active_count = 0
    else:
        active_count = len([item for item in items if _config_item_is_active(section, item)])
    archived_count = total_count - active_count

    return render_template('admin/config_center.html',
                         section=section,
                         section_title=info['title'],
                         section_description=info['description'],
                         table_columns=info['columns'],
                         items=items,
                         total_count=total_count,
                         active_count=active_count,
                         archived_count=archived_count,
                         filter_status='all',
                         years=AcademicYear.query.order_by(AcademicYear.name.desc()).all(),
                         levels=AcademicLevel.query.order_by(AcademicLevel.sort_order, AcademicLevel.name).all(),
                         classes_by_level=classes_by_level,
                         class_student_counts=class_student_counts,
                         subject_class_map=subject_class_map,
                         subject_icons=subject_icons,
                         selected_subject_year_id=selected_subject_year_id,
                         selected_subject_level_id=selected_subject_level_id)


@admin_bp.route("/config-center/authenticate", methods=["POST"])
@login_required
def config_center_authenticate():
    """Authenticate administrator for Configuration Center access"""
    data = request.get_json(silent=True) or {}
    password = data.get('password', '')
    remember_duration = int(data.get('remember_duration', 0))

    # Verify administrator password
    # In production, use proper password hashing
    admin_user = User.query.filter_by(id=current_user.id).first()

    # Simple password verification (replace with proper hashing in production)
    if admin_user and admin_user.check_password(password):
        # Set session flag for Configuration Center access
        session['config_center_authenticated'] = True
        session['config_center_auth_time'] = datetime.utcnow().isoformat()

        if remember_duration > 0:
            session['config_center_auth_expiry'] = (datetime.utcnow() + timedelta(minutes=remember_duration)).isoformat()
        else:
            session['config_center_auth_expiry'] = (datetime.utcnow() + timedelta(minutes=5)).isoformat()

        audit("Configuration Center Access", f"Administrator {current_user.username} accessed Configuration Center")
        return jsonify({'success': True})
    else:
        return jsonify({'success': False, 'message': 'Invalid password'})


@admin_bp.route("/config-center/confirm-password", methods=["POST"])
@login_required
def config_center_confirm_password():
    """Confirm password for dangerous actions"""
    data = request.get_json(silent=True) or {}
    password = data.get('password', '')
    action = data.get('action', '')
    item_id = data.get('item_id')

    # Verify administrator password
    admin_user = User.query.filter_by(id=current_user.id).first()

    if admin_user and admin_user.check_password(password):
        # Log the action
        audit(f"Configuration Action: {action}", f"User {current_user.username} confirmed action on item {item_id}")
        return jsonify({'success': True})
    else:
        return jsonify({'success': False, 'message': 'Invalid password'})


@admin_bp.route("/config-center/academic-years")
@login_required
def config_academic_years():
    """Academic Years Management"""
    return redirect(url_for('admin.config_center', section='academic-years'))


@admin_bp.route("/config-center/exam-types")
@login_required
def config_exam_types():
    """Exam Types Management"""
    return redirect(url_for('admin.config_center', section='exam-types'))


@admin_bp.route("/config-center/levels")
@login_required
def config_levels():
    """Levels & Classes Management"""
    return redirect(url_for('admin.config_center', section='levels'))


@admin_bp.route("/config-center/subjects")
@login_required
def config_subjects():
    """Subjects Management"""
    return redirect(url_for(
        'admin.config_center',
        section='subjects',
        year_id=request.args.get('year_id') or None,
        level_id=request.args.get('level_id') or None,
    ))


@admin_bp.route("/config-center/promotion-rules")
@login_required
def config_promotion_rules():
    """Promotion Rules Management"""
    return redirect(url_for('admin.config_center', section='promotion-rules'))


@admin_bp.route("/config-center/result-settings")
@login_required
def config_result_settings():
    """Result Settings Management"""
    return redirect(url_for('admin.config_center', section='result-settings'))


@admin_bp.route("/config-center/system-defaults")
@login_required
def config_system_defaults():
    """System Defaults Management"""
    return redirect(url_for('admin.config_center', section='system-defaults'))


@admin_bp.route("/config-center/audit-logs")
@login_required
def config_audit_logs():
    """Audit Logs View"""
    return redirect(url_for('admin.config_center', section='audit-logs'))


@admin_bp.route("/config-center/archive")
@login_required
def config_archive():
    """Archive Management"""
    return redirect(url_for('admin.config_center', section='archive'))


# Configuration Center CRUD API Endpoints
@admin_bp.route("/config-center/api/academic-years", methods=["POST"])
@login_required
def config_create_academic_year():
    """Create new academic year"""
    from flask import jsonify

    data = request.get_json(silent=True) or {}
    name = data.get('name', '').strip()

    if not name:
        return jsonify({'success': False, 'message': 'Name is required'})

    try:
        # Check if academic year already exists
        existing = AcademicYear.query.filter_by(name=name).first()
        if existing:
            return jsonify({'success': False, 'message': 'Academic year already exists'})

        academic_year = AcademicYear(
            name=name,
            is_current=False
        )
        db.session.add(academic_year)
        db.session.commit()

        audit("Configuration Center", f"Created academic year: {name}")
        return jsonify({'success': True, 'message': 'Academic year created successfully', 'data': {'id': academic_year.id, 'name': academic_year.name}})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)})


@admin_bp.route("/config-center/api/academic-years/<int:year_id>", methods=["PUT"])
@login_required
def config_update_academic_year(year_id):
    """Update academic year"""
    from flask import jsonify

    data = request.get_json(silent=True) or {}
    year = AcademicYear.query.get_or_404(year_id)
    name = data.get('name', year.name).strip()
    if not name:
        return jsonify({'success': False, 'message': 'Name is required'})
    if _duplicate_exists(AcademicYear, {'name': name}, exclude_id=year.id):
        return jsonify({'success': False, 'message': 'Academic year already exists'})
    old_name = year.name
    year.name = name

    try:
        db.session.commit()
        _audit_config_change("Configuration Center Updated", "academic-years", year, old_value=old_name, new_value=year.name)
        return jsonify({'success': True, 'message': 'Academic year updated successfully'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)})


@admin_bp.route("/config-center/api/academic-years/<int:year_id>/activate", methods=["POST"])
@login_required
def config_activate_academic_year(year_id):
    """Set academic year as current"""
    from flask import jsonify

    year = AcademicYear.query.get_or_404(year_id)

    try:
        # Deactivate all other years
        AcademicYear.query.update({'is_current': False})

        # Activate this year
        year.is_current = True
        db.session.commit()

        audit("Configuration Center", f"Activated academic year: {year.name}")
        return jsonify({'success': True, 'message': 'Academic year activated successfully'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)})


@admin_bp.route("/config-center/api/academic-years/<int:year_id>/deactivate", methods=["POST"])
@login_required
def config_deactivate_academic_year(year_id):
    """Deactivate academic year"""
    from flask import jsonify

    year = AcademicYear.query.get_or_404(year_id)

    try:
        year.is_current = False
        db.session.commit()

        audit("Configuration Center", f"Deactivated academic year: {year.name}")
        return jsonify({'success': True, 'message': 'Academic year deactivated successfully'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)})


@admin_bp.route("/config-center/api/exam-types", methods=["POST"])
@login_required
def config_create_exam_type():
    """Create new exam type"""
    from flask import jsonify

    data = request.get_json(silent=True) or {}
    name = data.get('name', '').strip()
    short_code = data.get('short_code', '').strip()
    weight_percentage = _parse_float(data.get('weight_percentage'), 0.0)
    academic_year_id = _parse_int(data.get('academic_year_id'))

    if not name:
        return jsonify({'success': False, 'message': 'Name is required'})
    if not academic_year_id or not AcademicYear.query.get(academic_year_id):
        return jsonify({'success': False, 'message': 'Academic year is required'})

    try:
        if _duplicate_exists(Exam, {'name': name, 'academic_year_id': academic_year_id}):
            return jsonify({'success': False, 'message': 'Exam type already exists for this academic year'})
        exam = Exam(
            name=name,
            short_code=short_code,
            weight_percentage=weight_percentage,
            academic_year_id=academic_year_id,
            is_active=True
        )
        db.session.add(exam)
        db.session.commit()

        audit("Configuration Center", f"Created exam type: {name}")
        return jsonify({'success': True, 'message': 'Exam type created successfully', 'data': {'id': exam.id, 'name': exam.name}})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)})


@admin_bp.route("/config-center/api/exam-types/<int:exam_id>", methods=["PUT"])
@login_required
def config_update_exam_type(exam_id):
    """Update exam type"""
    from flask import jsonify

    data = request.get_json(silent=True) or {}
    exam = Exam.query.get_or_404(exam_id)

    name = data.get('name', exam.name).strip()
    academic_year_id = _parse_int(data.get('academic_year_id'), exam.academic_year_id)
    if not name:
        return jsonify({'success': False, 'message': 'Name is required'})
    if not academic_year_id or not AcademicYear.query.get(academic_year_id):
        return jsonify({'success': False, 'message': 'Academic year is required'})
    if _duplicate_exists(Exam, {'name': name, 'academic_year_id': academic_year_id}, exclude_id=exam.id):
        return jsonify({'success': False, 'message': 'Exam type already exists for this academic year'})

    old_value = {'name': exam.name, 'academic_year_id': exam.academic_year_id, 'short_code': exam.short_code, 'weight_percentage': exam.weight_percentage}
    exam.name = name
    exam.short_code = data.get('short_code', exam.short_code)
    exam.weight_percentage = _parse_float(data.get('weight_percentage'), exam.weight_percentage)
    exam.academic_year_id = academic_year_id

    try:
        db.session.commit()
        _audit_config_change("Configuration Center Updated", "exam-types", exam, old_value=old_value, new_value={'name': exam.name, 'academic_year_id': exam.academic_year_id, 'short_code': exam.short_code, 'weight_percentage': exam.weight_percentage})
        return jsonify({'success': True, 'message': 'Exam type updated successfully'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)})


@admin_bp.route("/config-center/api/exam-types/<int:exam_id>/activate", methods=["POST"])
@login_required
def config_activate_exam_type(exam_id):
    """Activate exam type"""
    exam = Exam.query.get_or_404(exam_id)
    try:
        exam.is_active = True
        db.session.commit()
        _audit_config_change("Configuration Center Activated", "exam-types", exam)
        return jsonify({'success': True, 'message': 'Exam type activated successfully'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)})


@admin_bp.route("/config-center/api/exam-types/<int:exam_id>/deactivate", methods=["POST"])
@login_required
def config_deactivate_exam_type(exam_id):
    """Deactivate exam type before archive/delete"""
    exam = Exam.query.get_or_404(exam_id)
    try:
        exam.is_active = False
        db.session.commit()
        _audit_config_change("Configuration Center Deactivated", "exam-types", exam)
        return jsonify({'success': True, 'message': 'Exam type deactivated successfully'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)})


@admin_bp.route("/config-center/api/levels", methods=["POST"])
@login_required
def config_create_level():
    """Create new academic level"""
    from flask import jsonify

    data = request.get_json(silent=True) or {}
    name = data.get('name', '').strip()
    sort_order = _parse_int(data.get('sort_order'))

    if not name:
        return jsonify({'success': False, 'message': 'Name is required'})

    try:
        from sqlalchemy import func
        if _duplicate_exists(AcademicLevel, {'name': name}):
            return jsonify({'success': False, 'message': 'Academic level already exists'})
        max_sort = db.session.query(func.max(AcademicLevel.sort_order)).scalar() or 0

        level = AcademicLevel(
            name=name,
            is_active=True,
            sort_order=sort_order if sort_order is not None else max_sort + 1
        )
        db.session.add(level)
        db.session.commit()

        audit("Configuration Center", f"Created academic level: {name}")
        return jsonify({'success': True, 'message': 'Academic level created successfully', 'data': {'id': level.id, 'name': level.name}})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)})


@admin_bp.route("/config-center/api/levels/<int:level_id>", methods=["PUT"])
@login_required
def config_update_level(level_id):
    """Update academic level"""
    from flask import jsonify

    data = request.get_json(silent=True) or {}
    level = AcademicLevel.query.get_or_404(level_id)

    name = data.get('name', level.name).strip()
    if not name:
        return jsonify({'success': False, 'message': 'Name is required'})
    if _duplicate_exists(AcademicLevel, {'name': name}, exclude_id=level.id):
        return jsonify({'success': False, 'message': 'Academic level already exists'})
    old_value = {'name': level.name, 'sort_order': level.sort_order, 'is_active': level.is_active}
    level.name = name
    if 'sort_order' in data:
        level.sort_order = _parse_int(data.get('sort_order'), level.sort_order)
    if 'is_active' in data:
        level.is_active = bool(data.get('is_active'))

    try:
        db.session.commit()
        _audit_config_change("Configuration Center Updated", "levels", level, old_value=old_value, new_value={'name': level.name, 'sort_order': level.sort_order, 'is_active': level.is_active})
        return jsonify({'success': True, 'message': 'Academic level updated successfully'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)})


@admin_bp.route("/config-center/api/levels/<int:level_id>/activate", methods=["POST"])
@login_required
def config_activate_level(level_id):
    """Activate academic level"""
    from flask import jsonify

    level = AcademicLevel.query.get_or_404(level_id)

    try:
        level.is_active = True
        db.session.commit()

        audit("Configuration Center", f"Activated academic level: {level.name}")
        return jsonify({'success': True, 'message': 'Academic level activated successfully'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)})


@admin_bp.route("/config-center/api/levels/<int:level_id>/deactivate", methods=["POST"])
@login_required
def config_deactivate_level(level_id):
    """Deactivate academic level"""
    from flask import jsonify

    level = AcademicLevel.query.get_or_404(level_id)

    try:
        level.is_active = False
        db.session.commit()

        audit("Configuration Center", f"Deactivated academic level: {level.name}")
        return jsonify({'success': True, 'message': 'Academic level deactivated successfully'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)})


@admin_bp.route("/config-center/api/classes", methods=["POST"])
@login_required
def config_create_class():
    """Create new academic class inside a level"""
    data = request.get_json(silent=True) or {}
    name = data.get('name', '').strip()
    level_id = _parse_int(data.get('academic_level_id'))
    sort_order = _parse_int(data.get('sort_order'))

    if not name:
        return jsonify({'success': False, 'message': 'Class name is required'})
    if not level_id or not AcademicLevel.query.get(level_id):
        return jsonify({'success': False, 'message': 'Academic level is required'})

    try:
        if _duplicate_exists(AcademicClass, {'name': name, 'academic_level_id': level_id}):
            return jsonify({'success': False, 'message': 'Class already exists in this level'})
        from sqlalchemy import func
        max_sort = db.session.query(func.max(AcademicClass.sort_order)).filter_by(academic_level_id=level_id).scalar() or 0
        academic_class = AcademicClass(
            name=name,
            academic_level_id=level_id,
            sort_order=sort_order if sort_order is not None else max_sort + 1,
            is_active=True,
        )
        db.session.add(academic_class)
        db.session.commit()
        _audit_config_change("Configuration Center Created", "classes", academic_class)
        return jsonify({'success': True, 'message': 'Class created successfully', 'data': {'id': academic_class.id, 'name': academic_class.name}})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)})


@admin_bp.route("/config-center/api/classes/<int:class_id>", methods=["PUT"])
@login_required
def config_update_class(class_id):
    """Update academic class"""
    data = request.get_json(silent=True) or {}
    academic_class = AcademicClass.query.get_or_404(class_id)
    name = data.get('name', academic_class.name).strip()
    level_id = _parse_int(data.get('academic_level_id'), academic_class.academic_level_id)

    if not name:
        return jsonify({'success': False, 'message': 'Class name is required'})
    if not level_id or not AcademicLevel.query.get(level_id):
        return jsonify({'success': False, 'message': 'Academic level is required'})
    if _duplicate_exists(AcademicClass, {'name': name, 'academic_level_id': level_id}, exclude_id=academic_class.id):
        return jsonify({'success': False, 'message': 'Class already exists in this level'})

    old_value = {'name': academic_class.name, 'academic_level_id': academic_class.academic_level_id, 'sort_order': academic_class.sort_order, 'is_active': academic_class.is_active}
    academic_class.name = name
    academic_class.academic_level_id = level_id
    academic_class.sort_order = _parse_int(data.get('sort_order'), academic_class.sort_order)
    if 'is_active' in data:
        academic_class.is_active = bool(data.get('is_active'))

    try:
        db.session.commit()
        _audit_config_change("Configuration Center Updated", "classes", academic_class, old_value=old_value, new_value={'name': academic_class.name, 'academic_level_id': academic_class.academic_level_id, 'sort_order': academic_class.sort_order, 'is_active': academic_class.is_active})
        return jsonify({'success': True, 'message': 'Class updated successfully'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)})


@admin_bp.route("/config-center/api/classes/<int:class_id>/activate", methods=["POST"])
@login_required
def config_activate_class(class_id):
    academic_class = AcademicClass.query.get_or_404(class_id)
    try:
        academic_class.is_active = True
        db.session.commit()
        _audit_config_change("Configuration Center Activated", "classes", academic_class)
        return jsonify({'success': True, 'message': 'Class activated successfully'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)})


@admin_bp.route("/config-center/api/classes/<int:class_id>/deactivate", methods=["POST"])
@login_required
def config_deactivate_class(class_id):
    academic_class = AcademicClass.query.get_or_404(class_id)
    try:
        academic_class.is_active = False
        db.session.commit()
        _audit_config_change("Configuration Center Deactivated", "classes", academic_class)
        return jsonify({'success': True, 'message': 'Class deactivated successfully'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)})


@admin_bp.route("/config-center/api/subjects/<int:subject_id>/activate", methods=["POST"])
@login_required
def config_activate_subject(subject_id):
    subject = Subject.query.get_or_404(subject_id)
    try:
        subject.is_active = True
        db.session.commit()
        _audit_config_change("Configuration Center Activated", "subjects", subject)
        return jsonify({'success': True, 'message': 'Subject activated successfully'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)})


@admin_bp.route("/config-center/api/subjects/<int:subject_id>/deactivate", methods=["POST"])
@login_required
def config_deactivate_subject(subject_id):
    subject = Subject.query.get_or_404(subject_id)
    try:
        subject.is_active = False
        db.session.commit()
        _audit_config_change("Configuration Center Deactivated", "subjects", subject)
        return jsonify({'success': True, 'message': 'Subject deactivated (archived) successfully. You can now delete it permanently if needed.'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)})


@admin_bp.route("/config-center/api/subjects", methods=["POST"])
@login_required
def config_create_subject():
    """Create new subject"""
    from flask import jsonify

    data = request.get_json(silent=True) or {}
    name = data.get('name', '').strip()
    academic_level_id = _parse_int(data.get('academic_level_id'))
    max_score = _parse_float(data.get('max_score'), 100.0)
    sort_order = _parse_int(data.get('sort_order'))

    if not name:
        return jsonify({'success': False, 'message': 'Name is required'})

    try:
        from sqlalchemy import func
        if _duplicate_exists(Subject, {'name': name, 'academic_level_id': academic_level_id}):
            return jsonify({'success': False, 'message': 'Subject already exists for this level'})
        max_sort_query = db.session.query(func.max(Subject.sort_order))
        if academic_level_id:
            max_sort_query = max_sort_query.filter_by(academic_level_id=academic_level_id)
        max_sort = max_sort_query.scalar() or 0

        subject = Subject(
            name=name,
            academic_level_id=academic_level_id,
            max_score=max_score,
            sort_order=sort_order if sort_order is not None else max_sort + 1
        )
        db.session.add(subject)
        db.session.commit()

        audit("Configuration Center", f"Created subject: {name}")
        return jsonify({'success': True, 'message': 'Subject created successfully', 'data': {'id': subject.id, 'name': subject.name}})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)})


@admin_bp.route("/config-center/api/subjects/<int:subject_id>", methods=["PUT"])
@login_required
def config_update_subject(subject_id):
    """Update subject"""
    from flask import jsonify

    data = request.get_json(silent=True) or {}
    subject = Subject.query.get_or_404(subject_id)

    name = str(data.get('name', subject.name) or '').strip()
    academic_level_id = _parse_int(data.get('academic_level_id')) if 'academic_level_id' in data else subject.academic_level_id
    if not name:
        return jsonify({'success': False, 'message': 'Name is required'})
    if _duplicate_exists(Subject, {'name': name, 'academic_level_id': academic_level_id}, exclude_id=subject.id):
        return jsonify({'success': False, 'message': 'Subject already exists for this level'})

    old_value = {'name': subject.name, 'academic_level_id': subject.academic_level_id, 'max_score': float(subject.max_score or 0), 'sort_order': subject.sort_order}
    subject.name = name
    subject.academic_level_id = academic_level_id
    subject.max_score = _parse_float(data.get('max_score'), float(subject.max_score or 100))
    subject.sort_order = _parse_int(data.get('sort_order'), subject.sort_order)

    try:
        db.session.commit()
        _audit_config_change("Configuration Center Updated", "subjects", subject, old_value=old_value, new_value={'name': subject.name, 'academic_level_id': subject.academic_level_id, 'max_score': float(subject.max_score or 0), 'sort_order': subject.sort_order})
        return jsonify({'success': True, 'message': 'Subject updated successfully'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)})


@admin_bp.route("/config-center/api/<string:item_type>/<int:item_id>/delete", methods=["POST"])
@login_required
def config_delete_item(item_type, item_id):
    """Delete configuration item (with dependency check)"""
    Model = _config_model(item_type)
    if not Model:
        return jsonify({'success': False, 'message': 'Invalid item type'})

    item = Model.query.get_or_404(item_id)
    force = request.args.get('force') == 'true'

    if not force:
        if _config_item_is_active(item_type, item):
            return jsonify({'success': False, 'message': 'Active item protected. Deactivate this item before archive or permanent delete.'})

        dependencies = _config_dependencies(item_type, item_id)
        if dependencies:
            return jsonify({
                'success': False,
                'message': 'Cannot delete item with dependencies',
                'dependencies': dependencies
            })

    try:
        item_name = _item_display_name(item)
        if force:
            # First recursively delete all child dependencies
            _cascade_delete_config_item(item_type, item_id)
            
        # Re-fetch item to ensure it's in the current session transaction scope and delete it
        item = Model.query.get(item_id)
        if item:
            db.session.delete(item)
        db.session.commit()

        audit("Configuration Center", f"Deleted {item_type}: {item_name} (Force={force})")
        return jsonify({'success': True, 'message': 'Item deleted successfully'})
    except Exception as e:
        db.session.rollback()
        # Log full stack trace server-side for developer reference
        current_app.logger.exception(f"Force delete error for {item_type} ID {item_id}: {str(e)}")
        # Return a clean, non-technical message to the user
        return jsonify({
            'success': False,
            'message': 'Deletion blocked by database constraints. Some active dependencies could not be safely cleared.'
        })


@admin_bp.route("/config-center/api/<string:item_type>/<int:item_id>/archive", methods=["POST"])
@login_required
def config_archive_item(item_type, item_id):
    """Archive configuration item (soft delete)"""
    Model = _config_model(item_type)
    if not Model:
        return jsonify({'success': False, 'message': 'Invalid item type'})

    item = Model.query.get_or_404(item_id)
    if _config_item_is_active(item_type, item):
        return jsonify({'success': False, 'message': 'Active configuration cannot be archived. Deactivate it first.'})
    if not hasattr(item, 'is_current') and not hasattr(item, 'is_active'):
        return jsonify({'success': False, 'message': 'Archive is not supported for this item type with the current database model.'})

    try:
        # Archive by setting is_active/is_current to False
        if hasattr(item, 'is_current'):
            item.is_current = False
        if hasattr(item, 'is_active'):
            item.is_active = False

        db.session.commit()

        _audit_config_change("Configuration Center Archived", item_type, item)
        return jsonify({'success': True, 'message': 'Item archived successfully'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)})


@admin_bp.route("/config-center/api/<string:item_type>/<int:item_id>/restore", methods=["POST"])
@login_required
def config_restore_item(item_type, item_id):
    """Restore archived configuration item"""
    Model = _config_model(item_type)
    if not Model:
        return jsonify({'success': False, 'message': 'Invalid item type'})

    item = Model.query.get_or_404(item_id)
    if item_type == 'academic-years':
        return jsonify({'success': False, 'message': 'Academic years are restored by using Set as Current.'})
    if not hasattr(item, 'is_active'):
        return jsonify({'success': False, 'message': 'Restore is not supported for this item type with the current database model.'})

    try:
        # Restore by setting is_active to True
        if hasattr(item, 'is_active'):
            item.is_active = True

        db.session.commit()

        _audit_config_change("Configuration Center Restored", item_type, item)
        return jsonify({'success': True, 'message': 'Item restored successfully'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)})


@admin_bp.route("/config-center/api/<string:item_type>/<int:item_id>/dependencies", methods=["GET"])
@login_required
def config_item_dependencies(item_type, item_id):
    """Return real dependency information for configuration lifecycle decisions."""
    Model = _config_model(item_type)
    if not Model:
        return jsonify({'success': False, 'message': 'Invalid item type'}), 404
    item = Model.query.get_or_404(item_id)
    dependencies = _config_dependencies(item_type, item_id)
    return jsonify({
        'success': True,
        'item': {'id': item.id, 'name': _item_display_name(item), 'status': _config_status(item_type, item)},
        'dependencies': dependencies,
        'has_dependencies': bool(dependencies),
        'is_active': _config_item_is_active(item_type, item),
    })
