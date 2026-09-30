import secrets
from datetime import date, datetime
from io import BytesIO
import os
import re
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from flask import Blueprint, abort, current_app, flash, jsonify, make_response, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import joinedload
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from . import csrf, db
from .i18n import language_redirect
from .models import AcademicLevel, AcademicYear, AcademicYearSubject, Exam, ExamHall, ExamHallVersion, IdCardIssue, IncidentAction, IncidentCategory, IncidentReport, IncidentReportCategory, ReportVerification, Result, SeatMixerAssignment, SeverityLevel, Student, StudentComplaint, StudentComplaintReply, StudentFeedback, StudentFeedbackReply, StudentEnrollment, Subject
from .services import active_exam_for_student, attendance_uf_record, get_settings, result_payload, result_success_overlay_config, scoped_legacy_subjects, top_students_for_class
from .attendance_rules import normalize_attendance_status
from .enrollment_service import (
    enrollment_placement_for_student,
    get_enrollment_for_student_year,
    resolve_student_academic_context,
)
from .verification import verification_payload
from .import_wizard import normalize_student_phone
from .student_identity import find_student_by_code

public_bp = Blueprint("public", __name__)


@public_bp.route("/api/ping", methods=["GET", "HEAD"])
def ping():
    return jsonify(status="ok", timestamp=datetime.utcnow().isoformat()), 200


@public_bp.route("/favicon.ico", methods=["GET", "HEAD"])
def favicon():
    return ("", 204)


def incident_bool_setting(settings_dict, key, default=False):
    return str(settings_dict.get(key, "true" if default else "false")).lower() == "true"


def incident_reference_prefix(settings_dict):
    raw = (settings_dict.get("incident_reference_prefix") or "INC").strip().upper()
    return "".join(ch for ch in raw if ch.isalnum())[:10] or "INC"


def parse_incident_date(value):
    value = (value or "").strip()
    for fmt in ("%Y-%m-%d", "%B %d, %Y", "%d %B %Y", "%m/%d/%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise ValueError("Invalid incident date")


def parse_incident_time(value):
    value = (value or "").strip()
    for fmt in ("%H:%M", "%I:%M %p", "%I:%M%p"):
        try:
            return datetime.strptime(value, fmt).time()
        except ValueError:
            continue
    raise ValueError("Invalid incident time")


def incident_json_request():
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


def incident_form_error(message, errors=None, status=400):
    """Return a concrete error to the enhanced form and retain the legacy form flow."""
    errors = errors or [message]
    if incident_json_request():
        return jsonify(success=False, message=message, errors=errors), status
    for error in errors:
        flash(error, "danger")
    return redirect(request.url)


def is_other_lookup_value(value):
    return (value or "").strip().casefold() == "other"


def submitted_incident_category_ids():
    """Read multi-select categories while accepting legacy single-category forms."""
    raw_values = request.form.getlist("category_ids") or [request.form.get("category_id", "")]
    category_ids = []
    for raw_value in raw_values:
        try:
            category_id = int(raw_value)
        except (TypeError, ValueError):
            continue
        if category_id not in category_ids:
            category_ids.append(category_id)
    return category_ids


def incident_subjects_for_student(student, academic_year_id=None, placement=None):
    """Return only the configured subjects for the identified student's level."""
    if placement is None:
        placement = resolve_student_academic_context(student, academic_year_id) if academic_year_id else None
    if academic_year_id and not placement:
        return []
    enrollment = placement.get("enrollment") if placement else None
    level_id = placement.get("academic_level_id") if placement else student.academic_level_id
    if not level_id:
        return []
    if placement and placement.get("academic_year_level_id"):
        year_items = AcademicYearSubject.query.filter_by(
                academic_year_id=academic_year_id,
                academic_year_level_id=placement.get("academic_year_level_id"),
                subject_kind="exam",
                is_active=True,
            ).order_by(
                AcademicYearSubject.sort_order,
                AcademicYearSubject.name,
                AcademicYearSubject.id,
            ).all()
        mapped_ids = [row.legacy_subject_id for row in year_items if row.legacy_subject_id]
        # A year-aware scope with no subject bridge is incomplete setup; do
        # not silently widen it to the global subject table.
        if not mapped_ids:
            return []
        scoped_by_id = {
            subject.id: subject for subject in scoped_legacy_subjects(year_items)
        }
        return [
            scoped_by_id[subject_id]
            for subject_id in mapped_ids
            if subject_id in scoped_by_id
        ]
    return (
        Subject.query
        .filter(Subject.academic_level_id == level_id)
        .order_by(Subject.sort_order, Subject.name)
        .all()
    )


@public_bp.route("/")
def portal():
    return render_template("portal.html", settings=get_settings())


@public_bp.route("/language/<lang>")
def set_language(lang):
    return language_redirect(lang)


def _locked_exam_for_student(student, requested_exam_id=None):
    """Resolve the most relevant exam context for a locked student view."""
    query = (
        Exam.query.join(Result, Result.exam_id == Exam.id)
        .filter(Result.student_id == student.id)
    )
    if requested_exam_id:
        selected = query.filter(Exam.id == requested_exam_id).order_by(Exam.id.desc()).first()
        if selected:
            return selected
    selected = query.order_by(Exam.academic_year_id.desc(), Exam.id.desc()).first()
    if selected:
        return selected
    year_id = student.academic_year_id
    if not year_id:
        enrollment = (
            StudentEnrollment.query.filter_by(student_id=student.id)
            .order_by(StudentEnrollment.academic_year_id.desc(), StudentEnrollment.id.desc())
            .first()
        )
        year_id = enrollment.academic_year_id if enrollment else None
    if not year_id:
        return None
    return Exam.query.filter_by(academic_year_id=year_id).order_by(Exam.id.desc()).first()


def _locked_contact_links(settings):
    """Return icon-only contact links for the locked-result screen."""
    def clean_phone(value):
        raw = str(value or "").strip()
        if raw.lower().startswith("tel:"):
            raw = raw[4:]
        return re.sub(r"[^0-9+]", "", raw)

    def phone_link(value):
        value = clean_phone(value)
        return f"tel:{value}" if len(re.sub(r"[^0-9]", "", value)) >= 5 else ""

    def whatsapp_link(value):
        digits = re.sub(r"[^0-9]", "", str(value or ""))
        return f"https://wa.me/{digits}" if len(digits) >= 5 else ""

    def telegram_link(value):
        raw = str(value or "").strip()
        raw = re.sub(r"^https?://t\.me/", "", raw, flags=re.IGNORECASE)
        raw = raw.lstrip("@").split("?", 1)[0].strip("/")
        return f"https://t.me/{raw}" if raw else ""

    return [
        {"key": "whatsapp", "label": "WhatsApp", "class_name": "wa", "href": whatsapp_link(settings.get("whatsapp_url")), "icon": "whatsapp"},
        {"key": "mobile", "label": "Taleefan gacan", "class_name": "mb", "href": phone_link(settings.get("school_phone")), "icon": "mobile"},
        {"key": "landline", "label": "Land-line", "class_name": "ll", "href": phone_link(settings.get("call_url")), "icon": "landline"},
        {"key": "telegram", "label": "Telegram", "class_name": "tg", "href": telegram_link(settings.get("telegram_url")), "icon": "telegram"},
    ]


def locked_result_context(student, settings, requested_exam_id=None):
    """Build only real, optional data consumed by the locked-result design."""
    exam = _locked_exam_for_student(student, requested_exam_id)
    year_id = exam.academic_year_id if exam else student.academic_year_id
    placement = enrollment_placement_for_student(student, year_id) if year_id else None
    level_name = (
        placement.get("level_name") if placement else None
    ) or (student.academic_level.name if student.academic_level else None) or student.level or "-"
    class_name = (
        placement.get("class_name") if placement else None
    ) or (student.academic_class.name if student.academic_class else None) or (student.school_class.name if student.school_class else None) or "-"
    admin_reason = (student.lock_reason or "").strip()
    if admin_reason in {"", "Locked from advanced results.", "Outstanding clearance required."}:
        admin_reason = "Fadlan la xidhiidh xafiiska dugsiga ama maamulka si natiijadaada loo furo."
    guidance = "Fadlan la xidhiidh xafiiska dugsiga ama maamulka si natiijadaada loo furo."
    message_date = student.updated_at or datetime.utcnow()
    weekdays = ("Isniin", "Talaado", "Arbaca", "Khamiis", "Jimce", "Sabti", "Axad")
    admin_message = {
        "text": admin_reason,
        "from": settings.get("school_name") or "Maamulka Dugsiga",
        "date": f"{weekdays[message_date.weekday()]}, {message_date.strftime('%B %d, %Y - %I:%M %p')}",
    }

    return {
        "name": student.full_name or "-",
        "id": student.student_code or "-",
        "level": level_name,
        "klass": class_name,
        "photo": _public_asset_url(student.photo_path),
        "reason": guidance,
        "adminMessage": admin_message,
        "schoolName": settings.get("school_name") or "SULTAAN EMS",
        "schoolLogo": _public_asset_url(settings.get("logo_path")),
        "contacts": _locked_contact_links(settings),
    }


# =========================
# RESULT SUBMIT (MAIN FIX)
# =========================
@public_bp.route("/result", methods=["GET", "POST"])
def result():
    # Use POST -> Redirect -> GET so refreshing the result page never repeats
    # the lookup form submission.
    if request.method == "POST":
        redirect_values = {
            "student_id": request.form.get("student_id", "").strip(),
            "phone": request.form.get("phone", "").strip(),
        }
        for field in ("year_id", "exam_id"):
            value = request.form.get(field, type=int)
            if value is not None:
                redirect_values[field] = value
        return redirect(url_for("public.result", **redirect_values))

    student_id = request.args.get("student_id", "").strip()
    settings = get_settings()
    phone = request.args.get("phone", "").strip()
    selected_year_id = request.args.get("year_id", type=int)
    selected_exam_id = request.args.get("exam_id", type=int)

    student = find_student_by_code(student_id)

    if not student:
        return render_template(
            "portal.html",
            settings=get_settings(),
            invalid_student_id=student_id,
        )

    if settings.get("enable_phone_verification") == "on":
        submitted_phone = normalize_student_phone(phone)
        stored_phone = normalize_student_phone(student.phone)
        if not submitted_phone or stored_phone != submitted_phone:
            return render_template(
                "portal.html",
                settings=settings,
                error="Phone number verification failed."
            )

    if student.is_result_locked:
        return render_template(
            "locked_result.html",
            settings=get_settings(),
            student=student,
            locked_result=locked_result_context(student, settings, selected_exam_id),
        )

    available_exams = (
        Exam.query.join(Result, Result.exam_id == Exam.id)
        .filter(Result.student_id == student.id, Result.is_published.is_(True))
        .order_by(Exam.academic_year_id.desc(), Exam.id.desc())
        .distinct()
        .all()
    )

    if not available_exams:
        return render_template(
            "portal.html",
            settings=settings,
            unpublished_result=True,
            unpublished_student_id=student.student_code,
        )

    if not selected_exam_id:
        years = []
        seen_years = set()
        for exam_option in available_exams:
            if exam_option.academic_year and exam_option.academic_year_id not in seen_years:
                years.append(exam_option.academic_year)
                seen_years.add(exam_option.academic_year_id)
        return render_template(
            "portal.html",
            settings=settings,
            result_options={
                "student": student,
                "years": years,
                "exams": available_exams,
                "selected_year_id": selected_year_id or (years[0].id if years else None),
                "phone": phone,
            }
        )

    exam = next(
        (
            item for item in available_exams
            if item.id == selected_exam_id
            and (not selected_year_id or item.academic_year_id == selected_year_id)
        ),
        None,
    )

    payload = result_payload(student, exam=exam, public_only=True) if exam else None

    if not payload or not (payload.get("subjects") or payload.get("behavior_reports")):
        return render_template(
            "portal.html",
            settings=get_settings(),
            unpublished_result=True,
            unpublished_student_id=student.student_code,
        )

    result_scope = public_result_scope(student, exam)

    return render_template(
        "portal.html",
        settings=get_settings(),
        result=payload,
        result_scope=result_scope,
        generated_at=datetime.now(),
        feedback_access_token=feedback_access_token(student, exam),
        result_success_overlay=result_success_overlay_config(
            exam,
            payload.get("rank"),
            payload.get("average"),
            settings,
            letter_grade=(payload.get("overall_grade") or {}).get("grade") if isinstance(payload.get("overall_grade"), dict) else None,
        ),
    )


@public_bp.route("/result/view/<student_code>/<int:exam_id>")
def result_view(student_code, exam_id):
    """Open the published Student Result Portal for a specific student/exam."""
    student = find_student_by_code(student_code)
    if not student:
        abort(404)
    settings = get_settings()
    if student.is_result_locked:
        return render_template(
            "locked_result.html",
            settings=settings,
            student=student,
            locked_result=locked_result_context(student, settings, exam_id),
        ), 403

    exam = _published_exam_for_student(student, exam_id) or abort(404)
    payload = result_payload(student, exam=exam, public_only=True)
    if not payload or not (payload.get("subjects") or payload.get("behavior_reports")):
        abort(404)
    result_scope = public_result_scope(student, exam)
    return render_template(
        "portal.html",
        settings=settings,
        result=payload,
        result_scope=result_scope,
        generated_at=datetime.now(),
        feedback_access_token=feedback_access_token(student, exam),
        result_success_overlay=result_success_overlay_config(
            exam,
            payload.get("rank"),
            payload.get("average"),
            settings,
            letter_grade=(payload.get("overall_grade") or {}).get("grade")
            if isinstance(payload.get("overall_grade"), dict)
            else None,
        ),
    )


# =========================
# PRINT REPORT
# =========================
def _published_exam_for_student(student, requested_exam_id=None):
    """Resolve a published exam without using the student's mutable legacy year."""
    query = (
        Exam.query.join(Result, Result.exam_id == Exam.id)
        .filter(Result.student_id == student.id, Result.is_published.is_(True))
    )
    if requested_exam_id:
        return query.filter(Exam.id == requested_exam_id).order_by(Exam.id.desc()).first()

    year_ids = [
        year_id
        for year_id, in (
            StudentEnrollment.query
            .filter_by(student_id=student.id)
            .with_entities(StudentEnrollment.academic_year_id)
            .order_by(StudentEnrollment.academic_year_id.desc(), StudentEnrollment.id.desc())
            .all()
        )
        if year_id
    ]
    if student.academic_year_id and student.academic_year_id not in year_ids:
        year_ids.append(student.academic_year_id)
    for year_id in year_ids:
        exam = query.filter(Exam.academic_year_id == year_id).order_by(Exam.id.desc()).first()
        if exam:
            return exam
    return query.order_by(Exam.id.desc()).first()


def public_result_scope(student, exam):
    """Return the student's placement for the selected exam year only."""
    placement = enrollment_placement_for_student(student, exam.academic_year_id) or {}
    return {
        "class_name": placement.get("class_name") or "-",
        "level_name": placement.get("level_name") or "-",
        "academic_year_name": exam.academic_year.name if exam.academic_year else "-",
    }


def _portal_behavior_report_scope(student, exam, config_id, session_id):
    """Resolve one published student's exact Behavior session without widening scope."""
    from .behavior_reporting import get_behavior_report_data

    enrollment = get_enrollment_for_student_year(student.id, exam.academic_year_id)
    if not enrollment or enrollment.status not in {"active", "completed"}:
        abort(404)

    report = next(
        (
            item
            for item in get_behavior_report_data(student, exam)
            if item.get("configuration_id") == config_id
            and item.get("session_id") == session_id
            and item.get("available")
        ),
        None,
    )
    if not report:
        abort(404)
    return enrollment, report


def _render_portal_behavior_report(
    student_code, exam_id, config_id, session_id, report_kind, pdf_download=False
):
    """Render the existing report template for a published student result.

    This deliberately dispatches to the established report views in a nested
    request context. The browser view and the downloadable PDF therefore share
    the exact same template, report assembly, and canonical scoring projection.
    """
    student = find_student_by_code(student_code)
    if not student:
        abort(404)
    if student.is_result_locked:
        abort(403)
    exam = _published_exam_for_student(student, exam_id) or abort(404)
    enrollment, report = _portal_behavior_report_scope(student, exam, config_id, session_id)

    back_url = url_for("public.result_view", student_code=student.student_code, exam_id=exam.id)
    endpoint = (
        "public.behavior_reading_view"
        if report_kind == "behavior"
        else "public.attendance_reading_view"
    )
    download_url = url_for(
        endpoint,
        student_code=student.student_code,
        exam_id=exam.id,
        config_id=config_id,
        session_id=session_id,
        download=1,
        **(
            {"attendance_date": request.args.get("attendance_date")}
            if report_kind == "attendance" and request.args.get("attendance_date")
            else {}
        ),
    )
    route_args = {
        "year_id": exam.academic_year_id,
        "level_id": report["academic_year_level_id"],
        "class_id": enrollment.academic_year_class_id,
        "config_id": config_id,
        "session_id": session_id,
        "portal_read_only": 1,
        "portal_back_url": back_url,
        "portal_download_url": download_url,
    }
    if report_kind == "attendance":
        requested_date = request.args.get("attendance_date")
        selected_date = date.today()
        if requested_date:
            try:
                selected_date = date.fromisoformat(requested_date)
            except ValueError:
                selected_date = date.today()
        route_args["attendance_date"] = selected_date.isoformat()
        from .models import BehaviorAttendanceRecord

        attendance_records = (
            BehaviorAttendanceRecord.query.filter_by(
                student_enrollment_id=enrollment.id,
                behavior_configuration_id=config_id,
                behavior_session_id=session_id,
                status="active",
            )
            .with_entities(BehaviorAttendanceRecord.attendance_date)
            .order_by(BehaviorAttendanceRecord.attendance_date)
            .all()
        )
        month_keys = sorted(
            {(item.attendance_date.year, item.attendance_date.month) for item in attendance_records}
        )
        route_args["portal_download_filename"] = _attendance_download_filename(
            student,
            exam,
            selected_date,
            month_keys=month_keys,
        )
        target = url_for("behavior.attendance_report", enrollment_id=enrollment.id)
    else:
        target = url_for("behavior.student_report", enrollment_id=enrollment.id)
    if request.args.get("download") == "1":
        route_args["print"] = 1

    # The admin routes already own canonical report assembly. Calling the view
    # directly avoids a second student-facing reporting/calculation path while
    # keeping public access restricted to this published student's own scope.
    with current_app.test_request_context(target, query_string=route_args):
        if report_kind == "attendance":
            from .routes_behavior import attendance_report

            response = attendance_report(enrollment.id)
            if pdf_download:
                response = make_response(response)
                pdf_bytes = _html_report_to_pdf(
                    response.get_data(as_text=True),
                    request.url_root,
                )
                return send_file(
                    BytesIO(pdf_bytes),
                    as_attachment=True,
                    download_name=route_args["portal_download_filename"],
                    mimetype="application/pdf",
                    max_age=0,
                )
            return response
        from .routes_behavior import student_report

        response = make_response(student_report(enrollment.id))
        if pdf_download:
            pdf_bytes = _html_report_to_pdf(
                response.get_data(as_text=True),
                request.url_root,
            )
            return send_file(
                BytesIO(pdf_bytes),
                as_attachment=True,
                download_name=_behavior_download_filename(student, exam),
                mimetype="application/pdf",
                max_age=0,
            )
        return response


@public_bp.route("/behavior/<student_code>/<int:exam_id>/<int:config_id>/<int:session_id>/read")
def behavior_reading_view(student_code, exam_id, config_id, session_id):
    """Student read-only view of the exact Behavior PDF report."""
    return _render_portal_behavior_report(
        student_code,
        exam_id,
        config_id,
        session_id,
        "behavior",
        pdf_download=request.args.get("download") == "1",
    )


@public_bp.route("/behavior/<student_code>/<int:exam_id>/<int:config_id>/<int:session_id>/attendance/read")
def attendance_reading_view(student_code, exam_id, config_id, session_id):
    """Student read-only view of the exact Attendance PDF report."""
    if request.args.get("download") == "1":
        return _render_portal_behavior_report(
            student_code,
            exam_id,
            config_id,
            session_id,
            "attendance",
            pdf_download=True,
        )
    return _render_portal_behavior_report(
        student_code, exam_id, config_id, session_id, "attendance"
    )


@public_bp.route("/print/<student_code>")
def print_report(student_code):
    student_code = student_code.strip()
    settings = get_settings()
    requested_exam_id = request.args.get("exam_id", type=int)

    student = find_student_by_code(student_code)
    if not student:
        abort(404)

    if student.is_result_locked:
        return render_template(
            "locked_result.html",
            settings=settings,
            student=student,
            locked_result=locked_result_context(student, settings, requested_exam_id),
        ), 403

    exam = _published_exam_for_student(student, requested_exam_id) or abort(404)

    payload = result_payload(student, exam=exam, public_only=True)
    payload["verification"] = verification_payload(student, exam)
    payload["generated_at"] = datetime.now()
    result_scope = public_result_scope(student, exam)
    db.session.commit()

    return render_template(
        "print_report.html",
        result=payload,
        result_scope=result_scope,
        settings=settings,
        feedback_token=feedback_access_token(student, exam),
    )


def _safe_pdf_filename_part(value, fallback):
    invalid = '<>:"/\\|?*'
    cleaned = "".join(" " if ch in invalid or ord(ch) < 32 else ch for ch in str(value or ""))
    cleaned = " ".join(cleaned.split()).strip(" .")
    return cleaned or fallback


def _attendance_download_filename(student, exam, report_date, month_keys=None):
    """Build the stable, mobile-friendly filename for a portal attendance PDF."""
    month_abbreviations = (
        "Jan", "Feb", "Mar", "Apr", "May", "Jun",
        "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
    )
    month_keys = month_keys or [(report_date.year, report_date.month)]
    month_label = "-".join(
        month_abbreviations[month - 1]
        for _year, month in month_keys
    )
    name_parts = (student.full_name or "Student").split()[:2]
    student_name = _safe_pdf_filename_part(" ".join(name_parts), "Student")
    year_name = _safe_pdf_filename_part(
        exam.academic_year.name if exam.academic_year else None,
        "Academic Year",
    )
    return (
        f"{student_name} - Diiwaanka Xaadirka - "
        f"{month_label} - ({year_name}).pdf"
    )


def _behavior_download_filename(student, exam):
    """Build the stable filename used by the portal Behavior PDF."""
    name_parts = (student.full_name or "Student").split()[:2]
    student_name = _safe_pdf_filename_part(" ".join(name_parts), "Student")
    year_name = _safe_pdf_filename_part(
        exam.academic_year.name if exam.academic_year else None,
        "Academic Year",
    )
    return f"{student_name} - Diiwaanka Hab-dhaqanka - ({year_name}).pdf"


def _html_report_to_pdf(html_text, base_url):
    """Render the Reading View HTML without opening a browser print dialog."""
    chrome_candidates = [
        os.environ.get("CHROME_BIN"),
        os.environ.get("CHROMIUM_BIN"),
        shutil.which("google-chrome"),
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
        shutil.which("msedge"),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    ]
    chrome = next(
        (candidate for candidate in chrome_candidates if candidate and Path(candidate).exists()),
        None,
    )
    if chrome:
        with TemporaryDirectory(prefix="sultaan-attendance-pdf-") as temp_dir:
            html_path = Path(temp_dir) / "report.html"
            pdf_path = Path(temp_dir) / "report.pdf"
            html_path.write_text(
                html_text.replace("<head>", f'<head><base href="{base_url}">', 1),
                encoding="utf-8",
            )
            subprocess.run(
                [
                    chrome,
                    "--headless=new",
                    "--disable-gpu",
                    "--no-sandbox",
                    "--no-pdf-header-footer",
                    f"--print-to-pdf={pdf_path}",
                    html_path.as_uri(),
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=60,
            )
            return pdf_path.read_bytes()

    from xhtml2pdf import pisa

    pdf_buffer = BytesIO()
    pdf_result = pisa.CreatePDF(
        html_text,
        dest=pdf_buffer,
        encoding="UTF-8",
        capacity=100 * 1024 * 1024,
    )
    if pdf_result.err:
        abort(500, description="Attendance PDF could not be generated")
    return pdf_buffer.getvalue()


@public_bp.route("/download/<student_code>")
def download_report(student_code):
    """Render the canonical report and let the browser download it as PDF."""
    student_code = student_code.strip()
    settings = get_settings()
    requested_exam_id = request.args.get("exam_id", type=int)
    student = find_student_by_code(student_code)
    if not student:
        abort(404)
    if student.is_result_locked:
        return render_template(
            "locked_result.html",
            settings=settings,
            student=student,
            locked_result=locked_result_context(student, settings, requested_exam_id),
        ), 403

    exam = _published_exam_for_student(student, requested_exam_id) or abort(404)
    payload = result_payload(student, exam=exam, public_only=True)
    result_scope = public_result_scope(student, exam)
    payload["verification"] = verification_payload(student, exam)
    payload["generated_at"] = datetime.now()
    name_parts = (student.full_name or "Student").split()[:2]
    student_name = _safe_pdf_filename_part(" ".join(name_parts), "Student")
    exam_name = _safe_pdf_filename_part(exam.name, "Exam")
    year_name = _safe_pdf_filename_part(exam.academic_year.name, "Academic Year")
    filename = f"{student_name} - {exam_name} ({year_name}).pdf"
    return render_template(
        "print_report.html",
        result=payload,
        result_scope=result_scope,
        settings=settings,
        feedback_token=feedback_access_token(student, exam),
        download_mode=True,
        download_filename=filename,
    )


# =========================
# API ENDPOINT
# =========================
@public_bp.route("/api/results/<student_code>")
def api_result(student_code):
    student_code = student_code.strip()

    student = find_student_by_code(student_code)

    if not student:
        return jsonify({"ok": False, "message": "Student ID not found."}), 404

    if student.is_result_locked:
        return jsonify({
            "ok": False,
            "locked": True,
            "message": "Result temporarily withheld.",
            "reason": student.lock_reason
        }), 423

    exam = _published_exam_for_student(student)

    if not exam:
        return jsonify({"ok": False, "message": "No published result."}), 404

    payload = result_payload(student, exam=exam, public_only=True)
    from .behavior_reporting import serialize_behavior_reports

    result_scope = public_result_scope(student, exam)
    return jsonify({
        "ok": True,
        "student": {
            "id": student.student_code,
            "name": student.full_name,
            "mother_name": student.mother_name,
            "class": result_scope["class_name"],
            "academic_year": result_scope["academic_year_name"],
        },
        "exam": payload["exam"].name if payload.get("exam") else None,
        "subjects": payload["subjects"],
        "behavior_reports": serialize_behavior_reports(payload.get("behavior_reports", [])),
        "total": payload["total"],
        "average": payload["average"],
        "status": payload["status"],
        "grade": payload["overall_grade"],
    })


def _public_asset_url(path):
    """Build a browser-safe asset URL without exposing storage details."""
    if not path:
        return ""
    value = str(path)
    if value.startswith(("http://", "https://", "data:", "/static/")):
        return value
    return url_for("static", filename=value if value.startswith("uploads/") else f"uploads/{value}")


def _feedback_serializer():
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="sultaan-feedback-result-view")


def feedback_access_token(student, exam):
    """Grant the already-authorised public result viewer short-lived feedback access."""
    return _feedback_serializer().dumps({"student_id": student.id, "exam_id": exam.id})


def _feedback_context_from_request():
    token = (request.args.get("token") or (request.get_json(silent=True) or {}).get("token") or "").strip()
    if not token:
        return None, None, (jsonify(ok=False, message="Falcelinta lama xaqiijin karo."), 403)
    try:
        payload = _feedback_serializer().loads(token, max_age=60 * 60 * 4)
        student_id = int(payload.get("student_id"))
        exam_id = int(payload.get("exam_id"))
    except (BadSignature, SignatureExpired, TypeError, ValueError):
        return None, None, (jsonify(ok=False, message="Xiriirka Falcelinta wuu dhacay. Fadlan dib u fur natiijada."), 403)

    student = db.session.get(Student, student_id)
    exam = db.session.get(Exam, exam_id)
    has_published_result = bool(
        student and exam and Result.query.filter_by(student_id=student_id, exam_id=exam_id, is_published=True).first()
    )
    # Admin-generated class sheets can include an MG attendance record even
    # when that student has no published Result row yet. The signed token is
    # still required; only an authenticated admin may inspect that context.
    if not has_published_result and not current_user.is_authenticated:
        return None, None, (jsonify(ok=False, message="Natiijadan looma heli karo Falcelin."), 404)
    return student, exam, None


def _validate_student_signature(value):
    signature = str(value or "").strip()
    if signature and not signature.startswith("data:image/png;base64,"):
        return None, "Saxeexa lama aqoonsan."
    if len(signature) > 2_500_000:
        return None, "Saxeexu aad buu u weyn yahay."
    return signature, None


@public_bp.route("/api/falcelin/signature", methods=["GET"])
def get_feedback_signature():
    student, _exam, error = _feedback_context_from_request()
    if error:
        return error
    return jsonify(ok=True, signature=student.saved_signature_data or "")


@public_bp.route("/api/falcelin/signature", methods=["POST", "DELETE"])
@csrf.exempt
def save_feedback_signature():
    student, _exam, error = _feedback_context_from_request()
    if error:
        return error
    if request.method == "DELETE":
        student.saved_signature_data = None
        db.session.commit()
        return jsonify(ok=True, signature="")

    payload = request.get_json(silent=True) or {}
    signature, validation_error = _validate_student_signature(payload.get("signature"))
    if validation_error or not signature:
        return jsonify(ok=False, message=validation_error or "Fadlan marka hore ku saxiix."), 400
    student.saved_signature_data = signature
    db.session.commit()
    return jsonify(ok=True, signature=signature)


def _feedback_ref(prefix, model):
    year = datetime.utcnow().year
    for _ in range(12):
        ref = f"{prefix}-{year}-{secrets.token_hex(3).upper()}"
        if not model.query.filter_by(ref_number=ref).first():
            return ref
    return f"{prefix}-{year}-{secrets.token_hex(6).upper()}"


def _feedback_date(value):
    return value.strftime("%d %b %Y") if value else ""


def _feedback_iso(value):
    return f"{value.isoformat()}Z" if value else ""


def _feedback_clock(value):
    return value.strftime("%I:%M %p").lstrip("0") if value else ""


def _feedback_reply_payload(reply):
    if not reply:
        return None
    settings = get_settings()
    logo_path = settings.get("logo_path") or ""
    if logo_path and not str(logo_path).startswith(("http://", "https://", "data:")):
        logo_path = url_for("static", filename=str(logo_path).replace("\\", "/"))
    return {
        "office": reply.office_name or "Xafiiska Waxbarashada",
        "date": _feedback_date(reply.created_at),
        "created_at": _feedback_iso(reply.created_at),
        "time": _feedback_clock(reply.created_at),
        "message": reply.message,
        "logo": logo_path,
    }


def _feedback_item(entry):
    is_complaint = isinstance(entry, StudentComplaint)
    latest_reply = entry.replies[-1] if entry.replies else None
    if is_complaint:
        subject = entry.subject_name or None
        excerpt = (entry.details or "").strip()
        status = "answered" if latest_reply else "pending"
        item_type = "cabasho"
    else:
        subject = None
        reaction = (entry.reaction or "").replace("_", " ").title()
        excerpt = f"{entry.rating} star · {reaction}"
        if entry.comment:
            excerpt += f' — "{entry.comment.strip()}"'
        status = "answered" if latest_reply else "received"
        item_type = "falcelin"
    return {
        "ref": entry.ref_number,
        "type": item_type,
        "date": _feedback_date(entry.created_at),
        "created_at": _feedback_iso(entry.created_at),
        "subject": subject,
        "excerpt": excerpt[:260],
        "details": (entry.details if is_complaint else entry.comment) or "",
        "status": status,
        "delivery_status": "read" if entry.read_at else "delivered" if entry.delivered_at else "sent",
        "reply": _feedback_reply_payload(latest_reply),
    }


@public_bp.route("/api/falcelin/subjects")
def feedback_subjects():
    student, exam, error = _feedback_context_from_request()
    if error:
        return error
    placement = resolve_student_academic_context(student, exam.academic_year_id)
    if not placement:
        return jsonify(ok=True, subjects=[])
    enrollment = placement.get("enrollment")
    mapped_ids = []
    year_items = []
    if placement.get("academic_year_level_id"):
        year_items = AcademicYearSubject.query.filter_by(
                academic_year_id=exam.academic_year_id,
                academic_year_level_id=placement.get("academic_year_level_id"),
                subject_kind="exam",
                is_active=True,
            ).order_by(
                AcademicYearSubject.sort_order,
                AcademicYearSubject.name,
                AcademicYearSubject.id,
            ).all()
        mapped_ids = [row.legacy_subject_id for row in year_items if row.legacy_subject_id]
        behavior_items = AcademicYearSubject.query.filter_by(
                academic_year_id=exam.academic_year_id,
                academic_year_level_id=placement.get("academic_year_level_id"),
                subject_kind="behavior",
                is_active=True,
            ).order_by(
                AcademicYearSubject.sort_order,
                AcademicYearSubject.name,
                AcademicYearSubject.id,
            ).all()
    else:
        behavior_items = []
    level_id = placement.get("academic_level_id")
    if not level_id:
        return jsonify(ok=True, subjects=[])
    subject_query = (
        Subject.query.join(Result, Result.subject_id == Subject.id)
        .filter(
            Result.student_id == student.id,
            Result.exam_id == exam.id,
            Result.is_published.is_(True),
            Subject.is_active.is_(True),
        )
    )
    if placement.get("academic_year_level_id"):
        if mapped_ids:
            subject_query = subject_query.filter(Subject.id.in_(mapped_ids))
        else:
            subject_query = subject_query.filter(db.false())
    elif level_id:
        subject_query = subject_query.filter(Subject.academic_level_id == level_id)
    else:
        return jsonify(ok=True, subjects=[])
    subjects = subject_query.order_by(Subject.sort_order, Subject.name).distinct().all()
    if year_items and mapped_ids:
        scoped_by_id = {
            subject.id: subject for subject in scoped_legacy_subjects(year_items)
        }
        subjects = [scoped_by_id[subject.id] for subject in subjects if subject.id in scoped_by_id]

    subject_entries = [
        (getattr(subject, "sort_order", 0) or 0, subject.name)
        for subject in subjects
        if subject.name
    ]
    subject_entries.extend(
        (item.sort_order or 0, item.name)
        for item in behavior_items
        if item.name
    )
    subject_entries.sort(key=lambda entry: (entry[0], entry[1].casefold()))
    names = []
    seen_names = set()
    for _sort_order, name in subject_entries:
        normalized_name = name.strip()
        name_key = normalized_name.casefold()
        if normalized_name and name_key not in seen_names:
            names.append(normalized_name)
            seen_names.add(name_key)
    return jsonify(ok=True, subjects=names)


@public_bp.route("/api/falcelin/result-summary")
def feedback_result_summary():
    student, exam, error = _feedback_context_from_request()
    if error:
        return error
    payload = result_payload(student, exam=exam, public_only=True)
    rows = []
    for item in payload.get("subjects", []):
        grade = item.get("grade") or {}
        rows.append({
            "subject": item.get("subject") or "",
            "score": item.get("score"),
            "max_score": item.get("max_score"),
            "grade": grade.get("grade") if isinstance(grade, dict) else str(grade or ""),
            "is_uf": bool(item.get("is_uf")),
        })
    for behavior in payload.get("behavior_reports", []):
        grade = behavior.get("grade") or {}
        score = behavior.get("session_score")
        maximum = behavior.get("session_maximum")
        rows.append({
            "subject": behavior.get("subject_name") or "HAB-DHAQAN",
            "score": float(score) if score is not None else None,
            "max_score": float(maximum) if maximum is not None else None,
            "grade": grade.get("grade") if isinstance(grade, dict) else str(grade or ""),
            "is_uf": False,
        })
    overall = payload.get("overall_grade") or {}
    return jsonify(ok=True, subjects=rows, total=payload.get("total"), max_total=payload.get("max_total"), average=payload.get("average"), grade=overall.get("grade") if isinstance(overall, dict) else str(overall or ""))


@public_bp.route("/api/falcelin/mg-details")
def feedback_mg_details():
    """Return the real attendance context behind one Ma Gelin subject."""
    student, exam, error = _feedback_context_from_request()
    if error:
        return error

    placement = resolve_student_academic_context(student, exam.academic_year_id)
    if not placement:
        return jsonify(ok=False, message="Ardaygani kuma jiro sanadkan natiijada."), 404
    enrollment = placement.get("enrollment")
    student_level_id = placement.get("academic_level_id")
    subject_id = request.args.get("subject_id", type=int)
    year_items = []
    scoped_subject_by_id = {}
    if placement.get("academic_year_level_id"):
        year_items = AcademicYearSubject.query.filter_by(
            academic_year_id=exam.academic_year_id,
            academic_year_level_id=placement.get("academic_year_level_id"),
            subject_kind="exam",
            is_active=True,
        ).order_by(
            AcademicYearSubject.sort_order,
            AcademicYearSubject.name,
            AcademicYearSubject.id,
        ).all()
        scoped_subject_by_id = {
            item.id: item for item in scoped_legacy_subjects(year_items)
        }
    subject = db.session.get(Subject, subject_id) if subject_id else None
    mapped_subject_ids = {
        row.legacy_subject_id for row in year_items
        if row.legacy_subject_id
    } if placement.get("academic_year_level_id") else set()
    if not subject and request.args.get("subject") and student_level_id:
        requested_subject_name = request.args.get("subject").strip().casefold()
        subject = next(
            (
                item for item in scoped_subject_by_id.values()
                if (item.name or "").strip().casefold() == requested_subject_name
            ),
            None,
        )
        if subject is None:
            subject = (
                Subject.query
                .filter(
                    Subject.academic_level_id == student_level_id,
                    Subject.name == request.args.get("subject").strip(),
                )
                .order_by(Subject.id.asc())
                .first()
            )
    # Keep legacy setup rows usable when their old nullable flag is NULL.
    if not subject or subject.is_active is False:
        return jsonify(ok=False, message="Macluumaadka maaddadan lama heli karo."), 404

    if mapped_subject_ids and subject.id not in mapped_subject_ids:
        return jsonify(ok=False, message="Maaddadani kuma jirto sanadkan iyo heerka ardeygan."), 404
    if placement.get("academic_year_level_id") and not mapped_subject_ids:
        return jsonify(ok=False, message="Maaddooyinka sanadkan lama dejin."), 404
    if not mapped_subject_ids and (not student_level_id or subject.academic_level_id != student_level_id):
        return jsonify(ok=False, message="Maaddadani kuma jirto heerka ardeygan."), 404

    if scoped_subject_by_id:
        subject = scoped_subject_by_id.get(subject.id, subject)

    record = attendance_uf_record(exam, student.id, subject.id)
    if not record:
        return jsonify(ok=False, message="Attendance record-ka Ma Gelin lama helin."), 404

    status_labels = {
        "absent": "Maqnaansho / Ma aaddan soo xaadirin",
        "sick": "Xanuun / Cudur daar",
        "emergency": "Xaalad degdeg ah",
        "excused": "Fasax la oggolaaday",
    }
    status_key = normalize_attendance_status(record.status)
    session = record.exam_session
    exam_date = session.session_date if session else record.attendance_date
    hall = record.exam_hall.name if record.exam_hall else None
    if not hall and record.school_class:
        hall = record.school_class.name
    somali_weekdays = ("Isniin", "Talaada", "Arabaca", "Khamiis", "Jumca", "Sabti", "Axad")
    somali_months = (
        "Janaayo", "Febraayo", "Maarso", "Abriil", "May", "Juun",
        "Luulyo", "Agoosto", "Sebtembar", "Oktoobar", "Nofeember", "Diseembar",
    )
    exam_date_text = (
        f"{somali_weekdays[exam_date.weekday()]}, {somali_months[exam_date.month - 1]} {exam_date.day}, {exam_date.year}."
        if exam_date else "Taariikh aan la cayimin"
    )
    return jsonify(
        ok=True,
        subject_name=subject.name,
        session=session.sitting_label if session else "Fadhi aan la cayimin",
        exam_date=exam_date_text,
        exam_room=hall or "Fasal-imtixaan aan la cayimin",
        absence_reason=(record.note or "").strip() or status_labels.get(status_key, status_key.title()),
        registered_by=(record.marked_by.full_name if record.marked_by else "Attendance"),
        recorded_time=record.recorded_at.strftime("%H:%M") if record.recorded_at else "",
    )


@public_bp.route("/api/falcelin", methods=["POST"])
@csrf.exempt
def submit_feedback():
    student, exam, error = _feedback_context_from_request()
    if error:
        return error
    payload = request.get_json(silent=True) or {}
    try:
        rating = int(payload.get("rating"))
    except (TypeError, ValueError):
        rating = 0
    reaction = str(payload.get("reaction") or "").strip().lower()
    comment = str(payload.get("comment") or "").strip()
    if rating not in {1, 2, 3, 4, 5} or reaction not in {"like", "love", "care", "wow"} or not comment:
        return jsonify(ok=False, message="Fadlan buuxi xiddigaha, falcelinta, iyo faallada."), 400
    if len(comment) > 2000:
        return jsonify(ok=False, message="Faalladu aad bay u dheertahay."), 400
    entry = StudentFeedback(
        student_id=student.id,
        exam_id=exam.id,
        ref_number=_feedback_ref("FLC", StudentFeedback),
        rating=rating,
        reaction=reaction,
        comment=comment,
    )
    db.session.add(entry)
    db.session.commit()
    return jsonify(ok=True, ref=entry.ref_number, date=_feedback_date(entry.created_at))


@public_bp.route("/api/cabasho", methods=["POST"])
@csrf.exempt
def submit_complaint():
    student, exam, error = _feedback_context_from_request()
    if error:
        return error
    payload = request.get_json(silent=True) or {}
    complaint_type = str(payload.get("type") or "").strip().lower()
    subject_name = str(payload.get("subject") or "").strip()
    details = str(payload.get("details") or "").strip()
    signature = str(payload.get("signature") or "").strip() or (student.saved_signature_data or "")
    valid_types = {"maaddo", "wadar", "celcelis", "system", "kale"}
    if complaint_type not in valid_types or not details:
        return jsonify(ok=False, message="Fadlan buuxi dhammaan xogta cabashada."), 400
    signature, signature_error = _validate_student_signature(signature)
    if signature_error:
        return jsonify(ok=False, message=signature_error), 400
    if complaint_type == "maaddo" and not subject_name:
        return jsonify(ok=False, message="Fadlan dooro maaddada cabashada."), 400
    if len(details) > 5000 or len(signature) > 2_500_000:
        return jsonify(ok=False, message="Cabashada ama saxeexu aad bay u weyn yihiin."), 400
    entry = StudentComplaint(
        student_id=student.id,
        exam_id=exam.id,
        ref_number=_feedback_ref("CAB", StudentComplaint),
        complaint_type=complaint_type,
        subject_name=subject_name if complaint_type == "maaddo" else None,
        details=details,
        signature_data=signature,
        status="pending",
    )
    db.session.add(entry)
    db.session.commit()
    return jsonify(ok=True, ref=entry.ref_number, date=_feedback_date(entry.created_at))


@public_bp.route("/api/falcelin/replies")
def feedback_replies():
    student, exam, error = _feedback_context_from_request()
    if error:
        return error
    feedback_entries = StudentFeedback.query.filter_by(student_id=student.id, exam_id=exam.id).all()
    complaints = StudentComplaint.query.filter_by(student_id=student.id, exam_id=exam.id).all()
    entries = sorted([*feedback_entries, *complaints], key=lambda entry: entry.created_at, reverse=True)
    unread = sum(1 for entry in entries if entry.replies and not entry.read_by_student)
    return jsonify(ok=True, items=[_feedback_item(entry) for entry in entries], unread_count=unread)


@public_bp.route("/api/falcelin/replies/read", methods=["PATCH"])
@csrf.exempt
def mark_feedback_replies_read():
    student, exam, error = _feedback_context_from_request()
    if error:
        return error
    for entry in StudentFeedback.query.filter_by(student_id=student.id, exam_id=exam.id).all():
        if entry.replies:
            entry.read_by_student = True
    for entry in StudentComplaint.query.filter_by(student_id=student.id, exam_id=exam.id).all():
        if entry.replies:
            entry.read_by_student = True
    db.session.commit()
    return jsonify(ok=True)


@public_bp.route("/api/top-students/<student_code>")
def api_top_students(student_code):
    """Return the published Top 10 for the viewer's class and chosen exam."""
    normalized_code = student_code.strip()
    student = find_student_by_code(normalized_code)
    exam_id = request.args.get("exam_id", type=int)
    if not student or not exam_id:
        return jsonify(ok=False, message="Student and examination are required."), 404
    if student.is_result_locked:
        return jsonify(ok=False, message="Result temporarily withheld."), 423

    exam = (
        Exam.query.join(Result, Result.exam_id == Exam.id)
        .filter(Exam.id == exam_id, Result.student_id == student.id, Result.is_published.is_(True))
        .first()
    )
    if not exam:
        return jsonify(ok=False, message="Published examination not found."), 404

    placement = resolve_student_academic_context(student, exam.academic_year_id)
    if not placement:
        return jsonify(ok=False, message="Ardaygani kuma jiro sanadkan natiijada."), 404

    settings = get_settings()
    students = top_students_for_class(student, exam)
    for entry in students:
        entry["photo"] = _public_asset_url(entry.pop("photo_path", "")) or _public_asset_url(settings.get("result_dashboard_default_avatar"))
    class_name = students[0]["class_name"] if students else (
        placement.get("class_name") or placement.get("level_name") or "Class"
    )
    return jsonify(
        ok=True,
        class_name=class_name,
        academic_year=exam.academic_year.name if exam.academic_year else "",
        exam_type=exam.name,
        students=students,
    )


@public_bp.route("/verify/<token>")
def verify_report(token):
    settings = get_settings()
    if settings.get("verify_page_enabled") != "on":
        return render_template("verify.html", settings=settings, verified=False, disabled=True), 403
    record = ReportVerification.query.filter_by(token=token, is_valid=True).first()
    if not record:
        return render_template("verify.html", settings=settings, verified=False), 404
    payload = result_payload(record.student, exam=record.exam, public_only=True)
    return render_template("verify.html", settings=settings, verified=True, result=payload, verification=record)


@public_bp.route("/verify-id/<token>")
def verify_id_card(token):
    import logging
    logger = logging.getLogger(__name__)
    
    settings = get_settings()
    issue = IdCardIssue.query.filter_by(token=token).first()
    if not issue:
        return render_template("verify_id.html", settings=settings, verified=False), 404
    from .routes_id_cards import effective_issue_status, ensure_issue_dates
    if ensure_issue_dates(issue, settings=settings):
        db.session.commit()
    status = effective_issue_status(issue)
    placement = resolve_student_academic_context(issue.student, issue.academic_year_id) or {}
    if not placement and issue.student.academic_year_id == issue.academic_year_id:
        placement = {
            "source": "legacy",
            "academic_year_id": issue.academic_year_id,
            "class_name": issue.student.academic_class.name if issue.student.academic_class else issue.student.school_class.name if issue.student.school_class else None,
            "level_name": issue.student.academic_level.name if issue.student.academic_level else issue.student.level,
            "section_name": issue.student.academic_section.name if issue.student.academic_section else issue.student.section,
        }
    status_details = {
        "Active": {
            "label": "Firfircoon",
            "message": "Ardeygan waqti xaadirkan wuu firfircoon yahay.",
            "class_name": "status-active",
            "icon": "fa-shield-halved",
        },
        "Expired": {
            "label": "Wuu dhacay",
            "message": "Muddadii ansaxnimada kaarkani way dhammaatay.",
            "class_name": "status-expired",
            "icon": "fa-clock-rotate-left",
        },
        "Inactive": {
            "label": "Aan firfircoonayn",
            "message": "Kaarkani hadda ma aha mid firfircoon.",
            "class_name": "status-inactive",
            "icon": "fa-circle-pause",
        },
        "Blocked": {
            "label": "La xannibay",
            "message": "Kaarkani waxaa si ku meel gaar ah loo xannibay.",
            "class_name": "status-blocked",
            "icon": "fa-ban",
        },
    }.get(status, {
        "label": status,
        "message": "Xaaladda kaarkani lama xaqiijin.",
        "class_name": "status-unknown",
        "icon": "fa-circle-question",
    })
    
    # Debug logging - Student details
    logger.info(f"VERIFY STUDENT - Student ID: {issue.student.id}, Student Code: {issue.student.student_code}")
    logger.info(f"VERIFY STUDENT - Student academic_year_id: {issue.student.academic_year_id}")
    logger.info(f"VERIFY STUDENT - Student academic_level_id: {issue.student.academic_level_id}")
    logger.info(f"VERIFY STUDENT - Student academic_class_id: {issue.student.academic_class_id}")
    logger.info(f"VERIFY STUDENT - Student academic_section_id: {issue.student.academic_section_id}")
    
    requested_exam_id = request.args.get("exam_id", type=int)
    exam = (
        Exam.query.filter_by(id=requested_exam_id, academic_year_id=issue.academic_year_id).first()
        if requested_exam_id else None
    )
    exam = exam or active_exam_for_student(issue.student, preferred_year_id=issue.academic_year_id)

    seating = None
    if exam:
        seating_row = (
            SeatMixerAssignment.query
            .join(ExamHallVersion, SeatMixerAssignment.version_id == ExamHallVersion.id)
            .join(ExamHall, ExamHallVersion.exam_hall_id == ExamHall.id)
            .options(joinedload(SeatMixerAssignment.version).joinedload(ExamHallVersion.hall))
            .filter(
                SeatMixerAssignment.student_id == issue.student_id,
                ExamHall.exam_id == exam.id,
            )
            .order_by(SeatMixerAssignment.updated_at.desc(), SeatMixerAssignment.id.desc())
            .first()
        )
        if seating_row and seating_row.version and seating_row.version.hall:
            seating = {
                "hall_name": seating_row.version.hall.name,
                "seat_label": f"Saf {seating_row.row_number + 1} · Miis {seating_row.table_number + 1} · Kursi {seating_row.seat_number + 1}",
            }
    
    if exam:
        # Debug logging - Exam details
        logger.info(f"VERIFY STUDENT - Exam found: ID={exam.id}, Name={exam.name}")
        logger.info(f"VERIFY STUDENT - Exam academic_year_id: {exam.academic_year_id}")
        logger.info(f"VERIFY STUDENT - Exam academic_level_id: {exam.academic_level_id}")
        logger.info(f"VERIFY STUDENT - Exam academic_class_id: {exam.academic_class_id}")
        logger.info(f"VERIFY STUDENT - Exam academic_section_id: {exam.academic_section_id}")
        logger.info(f"VERIFY STUDENT - Exam is_active: {exam.is_active}")
        logger.info(f"VERIFY STUDENT - Exam is_published: {exam.is_published}")
    else:
        logger.warning(f"VERIFY STUDENT - No exam found through shared active exam lookup")
        # Log all exams for this academic year for debugging
        issue_year_exams = Exam.query.filter_by(academic_year_id=issue.academic_year_id).all()
        student_year_exams = Exam.query.filter_by(academic_year_id=issue.student.academic_year_id).all()
        logger.info(f"VERIFY STUDENT - ID card year exams: {[(e.id, e.name, e.is_active, e.is_published, e.academic_level_id, e.academic_class_id, e.academic_section_id) for e in issue_year_exams]}")
        logger.info(f"VERIFY STUDENT - Student year exams: {[(e.id, e.name, e.is_active, e.is_published, e.academic_level_id, e.academic_class_id, e.academic_section_id) for e in student_year_exams]}")
    
    return render_template(
        "verify_id.html",
        settings=settings,
        verified=True,
        issue=issue,
        placement=placement,
        display_status=status,
        status_details=status_details,
        exam=exam,
        seating=seating,
    )


@public_bp.route("/qr/<token>")
def qr_landing(token):
    """QR Landing Page with two action cards"""
    settings = get_settings()
    issue = IdCardIssue.query.filter_by(token=token).first()
    if not issue:
        return render_template("qr_landing.html", settings=settings, token=token, student=None), 404
    from .routes_id_cards import effective_issue_status, ensure_issue_dates
    if ensure_issue_dates(issue, settings=settings):
        db.session.commit()
    requested_exam_id = request.args.get("exam_id", type=int)
    qr_exam = (
        Exam.query.filter_by(id=requested_exam_id, academic_year_id=issue.academic_year_id).first()
        if requested_exam_id else None
    )
    return render_template(
        "qr_landing.html",
        settings=settings,
        token=token,
        student=issue.student,
        id_card_status=effective_issue_status(issue),
        qr_exam_id=qr_exam.id if qr_exam else None,
    )


@public_bp.route("/incident-report/<token>", methods=["GET", "POST"])
def incident_report_form(token):
    """Incident Report Form - Requires invigilator authentication"""
    from .routes_invigilator import current_invigilator, invigilator_login_required

    issue = (
        IdCardIssue.query
        .options(
            joinedload(IdCardIssue.student).joinedload(Student.school_class),
            joinedload(IdCardIssue.student).joinedload(Student.academic_year),
        )
        .filter_by(token=token)
        .first()
    )
    
    if not issue:
        settings = get_settings()
        return render_template("qr_landing.html", settings=settings, token=token, student=None), 404

    from .routes_id_cards import effective_issue_status, ensure_issue_dates
    if ensure_issue_dates(issue):
        db.session.commit()
    if effective_issue_status(issue) != "Active":
        return redirect(url_for("public.verify_id_card", token=token))

    # Authenticate before loading placement, subjects, exams, or settings. A
    # stale/unauthenticated QR request should finish with one lightweight
    # lookup instead of building the entire reporting form first.
    invigilator = current_invigilator()
    if not invigilator:
        from flask import session
        session["invigilator_next"] = request.url
        return redirect(url_for("invigilator.login"))

    settings = get_settings()
    from .models import IncidentReportSettings
    settings_dict = {
        setting.setting_key: setting.setting_value
        for setting in IncidentReportSettings.query.all()
    }
    allow_signature_reuse = incident_bool_setting(settings_dict, "allow_signature_reuse", True)

    student = issue.student
    placement = resolve_student_academic_context(student, issue.academic_year_id)
    student_subjects = incident_subjects_for_student(student, issue.academic_year_id, placement=placement)
    student_subject_ids = {subject.id for subject in student_subjects}

    requested_exam_id = request.args.get("exam_id", type=int)
    exam = (
        Exam.query.filter_by(id=requested_exam_id, academic_year_id=issue.academic_year_id).first()
        if requested_exam_id else None
    )
    exam = exam or active_exam_for_student(
        student,
        preferred_year_id=issue.academic_year_id,
        placement=placement,
        strict_preferred_year=True,
    )

    if request.method == "POST":
        # Generate report number
        from .models import IncidentReport
        import random
        import string
        category_ids = submitted_incident_category_ids()
        severity_id = request.form.get("severity_id")
        description = request.form.get("description", "").strip()
        actions_list = request.form.getlist("actions_taken")
        evidence_files = [file for file in request.files.getlist("evidence") if file and file.filename]
        signature_data = request.form.get("signature_data", "").strip()
        category_description = request.form.get("category_description", "").strip()
        action_description = request.form.get("action_description", "").strip()
        other_description = request.form.get("other_description", "").strip()
        if not signature_data and allow_signature_reuse:
            signature_data = invigilator.signature_data or ""

        validation_errors = []
        if incident_bool_setting(settings_dict, "require_category", True) and not category_ids:
            validation_errors.append("Fadlan xulo Qodob.")
        if incident_bool_setting(settings_dict, "require_severity", True) and not severity_id:
            validation_errors.append("Fadlan xulo Heerka Cakkirnaanta Xaaladda.")
        if incident_bool_setting(settings_dict, "require_description", True) and not description:
            validation_errors.append("Faafahinta dhacdada waa loo baahan yahay.")
        if incident_bool_setting(settings_dict, "require_signature", False) and not signature_data:
            validation_errors.append("Saxeexu waa loo baahan yahay.")
        if incident_bool_setting(settings_dict, "require_evidence", False) and not evidence_files:
            validation_errors.append("Fadlan soo geli daliil.")
        if incident_bool_setting(settings_dict, "require_subject", False) and not request.form.get("subject_id"):
            validation_errors.append("Fadlan xulo Maaddada.")
        if incident_bool_setting(settings_dict, "require_actions_taken", False) and not actions_list:
            validation_errors.append("Fadlan xulo ugu yaraan hal Ficil oo la Qaaday.")
        if incident_bool_setting(settings_dict, "require_incident_date", True) and not request.form.get("incident_date"):
            validation_errors.append("Taariikhda dhacdada waa loo baahan yahay.")
        if incident_bool_setting(settings_dict, "require_incident_time", True) and not request.form.get("incident_time"):
            validation_errors.append("Waqtiga dhacdada waa loo baahan yahay.")
        if validation_errors:
            return incident_form_error("Fadlan sax meelaha la calaamadeeyey.", validation_errors)

        subject_id = request.form.get("subject_id", type=int)
        if subject_id and subject_id not in student_subject_ids:
            return incident_form_error("Fadlan xulo maaddo loo qoondeeyey heerka ardeygan.")

        if not category_ids:
            default_category = IncidentCategory.query.order_by(IncidentCategory.sort_order, IncidentCategory.id).first()
            category_ids = [default_category.id] if default_category else []
        if not severity_id:
            default_severity = SeverityLevel.query.order_by(SeverityLevel.sort_order, SeverityLevel.id).first()
            severity_id = default_severity.id if default_severity else None
        if not category_ids or not severity_id:
            return incident_form_error("Qodobada dhacdada iyo heerarka cakkirnaanta waa in la habeeyaa ka hor gudbinta warbixinta.")
        if not description:
            description = "No description provided."

        try:
            categories_by_id = {
                category.id: category
                for category in IncidentCategory.query.filter(IncidentCategory.id.in_(category_ids)).all()
            }
            severity = SeverityLevel.query.filter_by(id=int(severity_id)).first()
        except (TypeError, ValueError):
            categories_by_id = {}
            severity = None
        selected_categories = [categories_by_id.get(category_id) for category_id in category_ids]
        if len(selected_categories) != len(category_ids) or any(category is None for category in selected_categories):
            return incident_form_error("Fadlan xulo Qodob dhacdo oo sax ah.")
        if not severity:
            return incident_form_error("Fadlan xulo Heerka Cakkirnaanta Xaaladda oo sax ah.")

        category_is_other = any(is_other_lookup_value(category.name) for category in selected_categories)
        action_has_other = any(is_other_lookup_value(action) for action in actions_list)

        if category_is_other and not category_description and not other_description:
            return incident_form_error("Fadlan faahfaahi Qodobka gaarka ah.")
        if action_has_other and not action_description and not other_description:
            return incident_form_error("Fadlan faahfaahi Ficilka gaarka ah.")

        if len(category_description) > 500:
            return incident_form_error("Faahfaahinta Qodobku waa inaysan ka badnaan 500 xaraf.")
        if len(action_description) > 500:
            return incident_form_error("Faahfaahinta Ficilku waa inaysan ka badnaan 500 xaraf.")
        if len(other_description) > 500:
            return incident_form_error("Faahfaahintu waa inaysan ka badnaan 500 xaraf.")

        # Legacy fallback if old form submitted single other_description
        if not category_description and category_is_other and other_description:
            category_description = other_description
        if not action_description and action_has_other and other_description:
            action_description = other_description

        # Combined fallback for other_description field for legacy queries
        legacy_other_combined = other_description or (" / ".join(filter(None, [category_description, action_description]))) or None

        if incident_bool_setting(settings_dict, "require_exam", False) and not exam:
            return incident_form_error("Ardeygan looma helin imtixaan firfircoon.")
        try:
            incident_date = parse_incident_date(request.form.get("incident_date"))
            incident_time = parse_incident_time(request.form.get("incident_time"))
        except ValueError:
            return incident_form_error("Fadlan geli taariikh iyo waqti dhacdo oo sax ah.")
        
        report_num = f"{incident_reference_prefix(settings_dict)}-{datetime.now().strftime('%Y%m%d')}-{''.join(random.choices(string.digits, k=4))}"
        
        # Handle actions taken as comma-separated string from checkboxes
        actions_taken = ", ".join(actions_list) if actions_list else ""
        
        # Create incident report
        report = IncidentReport(
            report_number=report_num,
            student_id=student.id,
            invigilator_id=invigilator.id,
            teacher_id=None,
            user_id=None,
            # Keep the first selected category as the legacy primary category.
            category_id=selected_categories[0].id,
            severity_id=severity.id,
            exam_id=exam.id if exam else None,
            subject_id=subject_id,
            exam_room=request.form.get("exam_room", ""),
            incident_date=incident_date,
            incident_time=incident_time,
            description=description,
            actions_taken=actions_taken,
            category_description=category_description or None,
            action_description=action_description or None,
            other_description=legacy_other_combined,
            signature_data=signature_data or None,
            status="Pending Review"
        )

        try:
            db.session.add(report)
            db.session.flush()
            db.session.add_all(
                [IncidentReportCategory(report_id=report.id, category_id=category.id) for category in selected_categories]
            )
            if signature_data and allow_signature_reuse:
                invigilator.signature_data = signature_data
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            current_app.logger.exception("Incident report submission failed")
            return incident_form_error("Warbixinta lama kaydin. Fadlan mar kale isku day.", status=500)
        
        # Handle file uploads if any (optional - report saves even if upload fails)
        if evidence_files:
            try:
                from .cloudinary_service import upload_image
                for file in evidence_files:
                    try:
                        file.stream.seek(0, 2)
                        file_size = file.stream.tell()
                        file.stream.seek(0)
                        file_path = upload_image(file, "incident/evidence")
                        from .models import IncidentAttachment
                        attachment = IncidentAttachment(
                            report_id=report.id,
                            file_path=file_path,
                            file_name=file.filename,
                            file_type=file.content_type or "application/octet-stream",
                            file_size=file_size,
                            uploaded_by_id=current_user.id if getattr(current_user, "is_authenticated", False) else None
                        )
                        db.session.add(attachment)
                    except Exception as upload_error:
                        # Log error but continue - report saves even if upload fails
                        current_app.logger.error(f"Failed to upload evidence file {file.filename}: {str(upload_error)}")
                db.session.commit()
            except Exception as e:
                # Log error but don't fail the entire report submission
                current_app.logger.error(f"File upload processing failed: {str(e)}")
        
        if incident_json_request():
            return jsonify(
                success=True,
                report_number=report.report_number,
                success_url=url_for("public.incident_report_success", token=token, report_id=report.id),
            )
        return render_template("incident_success.html", settings=settings, report=report, student=student, token=token)
    
    # GET request - show form
    categories = IncidentCategory.query.order_by(IncidentCategory.sort_order).all()
    other_category_ids = [category.id for category in categories if is_other_lookup_value(category.name)]
    severities = SeverityLevel.query.order_by(SeverityLevel.sort_order).all()
    actions = IncidentAction.query.order_by(IncidentAction.sort_order).all()
    subjects = student_subjects
    
    # Pre-compute current date/time for form defaults
    now = datetime.now()
    somali_months = ('Janaayo', 'Febraayo', 'Maarso', 'Abriil', 'May', 'Juun', 'Luulyo', 'Agoosto', 'Sebtembar', 'Oktoobar', 'Nofeember', 'Diseembar')
    current_date = f"{somali_months[now.month - 1]} {now.day:02d}, {now.year}"
    current_time = now.strftime('%I:%M %p')
    current_date_iso = now.strftime('%Y-%m-%d')
    current_time_24 = now.strftime('%H:%M')
    
    # Generate preview report number
    import random
    import string
    preview_report_num = f"{incident_reference_prefix(settings_dict)}-{datetime.now().strftime('%Y%m%d')}-{''.join(random.choices(string.digits, k=4))}"
    
    return render_template(
        "incident_form.html",
        settings=settings,
        incident_settings=settings_dict,
        token=token,
        student=student,
        categories=categories,
        other_category_ids=other_category_ids,
        severities=severities,
        actions=actions,
        subjects=subjects,
        current_date=current_date,
        current_time=current_time,
        current_date_iso=current_date_iso,
        current_time_24=current_time_24,
        preview_report_num=preview_report_num,
        current_user=current_user,
        invigilator=invigilator,
        allow_signature_reuse=allow_signature_reuse,
        exam=exam  # Pass the active exam to the template
    )


@public_bp.route("/incident-report/<token>/success/<int:report_id>")
def incident_report_success(token, report_id):
    """Completion view for enhanced submissions, protected by the QR token and invigilator session."""
    from .routes_invigilator import current_invigilator

    issue = IdCardIssue.query.filter_by(token=token).first_or_404()
    invigilator = current_invigilator()
    if not invigilator:
        return redirect(url_for("invigilator.login"))
    report = IncidentReport.query.filter_by(
        id=report_id,
        student_id=issue.student_id,
        invigilator_id=invigilator.id,
    ).first_or_404()
    return render_template("incident_success.html", settings=get_settings(), report=report, student=issue.student, token=token)
