import secrets
from io import BytesIO
from datetime import date, datetime

from flask import Blueprint, abort, current_app, flash, jsonify, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required
from sqlalchemy import func
from sqlalchemy.exc import SQLAlchemyError
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from . import csrf, db
from .i18n import language_redirect
from .models import AcademicLevel, AcademicYear, AcademicYearSubject, Exam, IdCardIssue, IncidentAction, IncidentCategory, IncidentReport, IncidentReportCategory, ReportVerification, Result, SeverityLevel, Student, StudentComplaint, StudentComplaintReply, StudentFeedback, StudentFeedbackReply, StudentEnrollment, Subject
from .services import active_exam_for_student, attendance_uf_record, get_settings, result_payload, result_success_overlay_config, top_students_for_class
from .attendance_rules import normalize_attendance_status
from .enrollment_service import enrollment_placement_for_student, get_enrollment_for_student_year
from .verification import verification_payload

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


def incident_subjects_for_student(student, academic_year_id=None):
    """Return only the configured subjects for the identified student's level."""
    enrollment = get_enrollment_for_student_year(student.id, academic_year_id) if academic_year_id else None
    level_id = (
        enrollment.academic_year_level.legacy_level_id
        if enrollment and enrollment.academic_year_level
        else student.academic_level_id
    )
    if not level_id and student.academic_class:
        level_id = student.academic_class.academic_level_id
    if not level_id:
        return []
    if enrollment:
        mapped_ids = [
            row.legacy_subject_id
            for row in AcademicYearSubject.query.filter_by(
                academic_year_id=academic_year_id,
                academic_year_level_id=enrollment.academic_year_level_id,
                is_active=True,
            ).all()
            if row.legacy_subject_id
        ]
        if mapped_ids:
            return Subject.query.filter(Subject.id.in_(mapped_ids), Subject.is_active.is_(True)).order_by(Subject.sort_order, Subject.name).all()
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


# =========================
# RESULT SUBMIT (MAIN FIX)
# =========================
@public_bp.route("/result", methods=["POST"])
def result():
    student_id = request.form.get("student_id", "").strip()
    settings = get_settings()
    phone = request.form.get("phone", "").strip()
    selected_year_id = request.form.get("year_id", type=int)
    selected_exam_id = request.form.get("exam_id", type=int)

    student = Student.query.filter(
        func.trim(Student.student_code) == student_id
    ).first()

    if not student:
        return render_template(
            "portal.html",
            settings=get_settings(),
            error="Ma jiro Student ID-ga aad gelisay."
        )

    if settings.get("enable_phone_verification") == "on":
        if not phone or (student.phone or "").strip() != phone:
            return render_template(
                "portal.html",
                settings=settings,
                error="Phone number verification failed."
            )

    if student.is_result_locked:
        return render_template(
            "locked_result.html",
            settings=get_settings(),
            student=student
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
            error="Natiijada ardaygan wali lama daabicin."
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

    if not payload or not payload.get("subjects"):
        return render_template(
            "portal.html",
            settings=get_settings(),
            error="Natiijada ardaygan wali lama daabicin."
        )

    return render_template(
        "portal.html",
        settings=get_settings(),
        result=payload,
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


@public_bp.route("/print/<student_code>")
def print_report(student_code):
    student_code = student_code.strip()
    settings = get_settings()

    student = Student.query.filter(
        func.trim(Student.student_code) == student_code
    ).first_or_404()

    if student.is_result_locked:
        return render_template(
            "locked_result.html",
            settings=settings,
            student=student
        ), 403

    requested_exam_id = request.args.get("exam_id", type=int)
    exam = _published_exam_for_student(student, requested_exam_id) or abort(404)

    payload = result_payload(student, exam=exam, public_only=True)
    payload["verification"] = verification_payload(student, exam)
    payload["generated_at"] = datetime.now()
    db.session.commit()

    return render_template(
        "print_report.html",
        result=payload,
        settings=settings,
        feedback_token=feedback_access_token(student, exam),
    )


def _safe_pdf_filename_part(value, fallback):
    invalid = '<>:"/\\|?*'
    cleaned = "".join(" " if ch in invalid or ord(ch) < 32 else ch for ch in str(value or ""))
    cleaned = " ".join(cleaned.split()).strip(" .")
    return cleaned or fallback


@public_bp.route("/download/<student_code>")
def download_report(student_code):
    """Download the currently viewed published result as a real PDF file."""
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    from xml.sax.saxutils import escape

    student_code = student_code.strip()
    settings = get_settings()
    student = Student.query.filter(func.trim(Student.student_code) == student_code).first_or_404()
    if student.is_result_locked:
        return render_template("locked_result.html", settings=settings, student=student), 403

    requested_exam_id = request.args.get("exam_id", type=int)
    exam = _published_exam_for_student(student, requested_exam_id) or abort(404)
    payload = result_payload(student, exam=exam, public_only=True)

    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=16 * mm,
        leftMargin=16 * mm,
        topMargin=14 * mm,
        bottomMargin=14 * mm,
        title=f"{student.full_name} - {exam.name}",
        author=settings.get("school_name") or "SULTAAN EMS",
    )
    styles = getSampleStyleSheet()
    school_style = ParagraphStyle("school", parent=styles["Title"], fontName="Helvetica-Bold", fontSize=16, leading=19, textColor=colors.HexColor("#102A5C"), alignment=TA_CENTER, spaceAfter=3)
    title_style = ParagraphStyle("title", parent=styles["Heading2"], fontName="Helvetica-Bold", fontSize=12, leading=15, textColor=colors.HexColor("#102A5C"), alignment=TA_CENTER, spaceAfter=10)
    body_style = ParagraphStyle("body", parent=styles["BodyText"], fontName="Helvetica", fontSize=9, leading=12, textColor=colors.HexColor("#1E293B"))
    story = [
        Paragraph(escape(str(settings.get("school_name") or "SULTAAN EMS")), school_style),
        Paragraph(escape(f"{exam.name} - {exam.academic_year.name}"), title_style),
    ]
    student_info = [
        [Paragraph("Student", body_style), Paragraph(escape(str(student.full_name)), body_style), Paragraph("Student ID", body_style), Paragraph(escape(str(student.student_code)), body_style)],
        [Paragraph("Class", body_style), Paragraph(escape(str(getattr(student.school_class, "name", "-"))), body_style), Paragraph("Academic Year", body_style), Paragraph(escape(str(exam.academic_year.name)), body_style)],
    ]
    info_table = Table(student_info, colWidths=[24 * mm, 64 * mm, 30 * mm, 58 * mm])
    info_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F7F5EF")),
        ("BOX", (0, 0), (-1, -1), .7, colors.HexColor("#D8CDAE")),
        ("INNERGRID", (0, 0), (-1, -1), .35, colors.HexColor("#E7E2D4")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 7), ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    story.extend([info_table, Spacer(1, 9 * mm)])
    rows = [[Paragraph("No.", body_style), Paragraph("Subject", body_style), Paragraph("Full Mark", body_style), Paragraph("Mark Obtained", body_style), Paragraph("Grade", body_style)]]
    for index, item in enumerate(payload.get("subjects", []), 1):
        grade = item.get("grade") or {}
        rows.append([
            str(index),
            Paragraph(escape(str(item.get("subject") or "-")), body_style),
            f"{item.get('max_score', 0):g}",
            "MG" if item.get("is_uf") else f"{item.get('score', 0):g}",
            "MG" if item.get("is_uf") else str(grade.get("grade") or "-"),
        ])
    result_table = Table(rows, colWidths=[14 * mm, 72 * mm, 28 * mm, 38 * mm, 28 * mm], repeatRows=1)
    result_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#102A5C")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), .45, colors.HexColor("#DCE3EC")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F8FAFC")]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (0, -1), "CENTER"), ("ALIGN", (2, 0), (-1, -1), "CENTER"),
        ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    story.extend([result_table, Spacer(1, 8 * mm)])
    summary = Table([
        [Paragraph("Total", body_style), Paragraph("Average", body_style), Paragraph("Grade", body_style), Paragraph("Rank", body_style)],
        [f"{payload.get('total', 0):g}/{payload.get('max_total', 0):g}", f"{payload.get('average', 0):.2f}%", str((payload.get("overall_grade") or {}).get("grade") or "-"), str(payload.get("rank") or "-")],
    ], colWidths=[42 * mm] * 4)
    summary.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E4EEF7")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#102A5C")),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), .45, colors.HexColor("#C9D7E6")),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    story.append(summary)
    document.build(story)
    buffer.seek(0)

    name_parts = (student.full_name or "Student").split()[:2]
    student_name = _safe_pdf_filename_part(" ".join(name_parts), "Student")
    exam_name = _safe_pdf_filename_part(exam.name, "Exam")
    year_name = _safe_pdf_filename_part(exam.academic_year.name, "Academic Year")
    filename = f"{student_name} - {exam_name} ({year_name}).pdf"
    return send_file(buffer, mimetype="application/pdf", as_attachment=True, download_name=filename)


# =========================
# API ENDPOINT
# =========================
@public_bp.route("/api/results/<student_code>")
def api_result(student_code):
    student_code = student_code.strip()

    student = Student.query.filter(
        func.trim(Student.student_code) == student_code
    ).first()

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

    placement = enrollment_placement_for_student(student, exam.academic_year_id) or {}
    return jsonify({
        "ok": True,
        "student": {
            "id": student.student_code,
            "name": student.full_name,
            "mother_name": student.mother_name,
            "class": placement.get("class_name") or (student.school_class.name if student.school_class else student.level or "-"),
            "academic_year": student.academic_year.name,
        },
        "exam": payload["exam"].name if payload.get("exam") else None,
        "subjects": payload["subjects"],
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
    enrollment = get_enrollment_for_student_year(student.id, exam.academic_year_id)
    mapped_ids = []
    if enrollment:
        mapped_ids = [
            row.legacy_subject_id
            for row in AcademicYearSubject.query.filter_by(
                academic_year_id=exam.academic_year_id,
                academic_year_level_id=enrollment.academic_year_level_id,
                is_active=True,
            ).all()
            if row.legacy_subject_id
        ]
    level_id = (
        enrollment.academic_year_level.legacy_level_id
        if enrollment and enrollment.academic_year_level
        else student.academic_level_id
    )
    if not level_id and student.academic_class:
        level_id = student.academic_class.academic_level_id
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
    if mapped_ids:
        subject_query = subject_query.filter(Subject.id.in_(mapped_ids))
    elif level_id:
        subject_query = subject_query.filter(Subject.academic_level_id == level_id)
    else:
        return jsonify(ok=True, subjects=[])
    subjects = subject_query.order_by(Subject.sort_order, Subject.name).distinct().all()
    return jsonify(ok=True, subjects=[subject.name for subject in subjects])


@public_bp.route("/api/falcelin/result-summary")
def feedback_result_summary():
    student, exam, error = _feedback_context_from_request()
    if error:
        return error
    payload = result_payload(student, exam=exam, public_only=True)
    rows = []
    for item in payload.get("subjects", []):
        grade = item.get("grade") or {}
        rows.append({"subject": item.get("subject") or "", "score": item.get("score"), "max_score": item.get("max_score"), "grade": grade.get("grade") if isinstance(grade, dict) else str(grade or "")})
    overall = payload.get("overall_grade") or {}
    return jsonify(ok=True, subjects=rows, total=payload.get("total"), max_total=payload.get("max_total"), average=payload.get("average"), grade=overall.get("grade") if isinstance(overall, dict) else str(overall or ""))


@public_bp.route("/api/falcelin/mg-details")
def feedback_mg_details():
    """Return the real attendance context behind one Ma Gelin subject."""
    student, exam, error = _feedback_context_from_request()
    if error:
        return error

    enrollment = get_enrollment_for_student_year(student.id, exam.academic_year_id)
    student_level_id = (
        enrollment.academic_year_level.legacy_level_id
        if enrollment and enrollment.academic_year_level
        else student.academic_level_id or (
            student.academic_class.academic_level_id if student.academic_class else None
        )
    )
    if not student_level_id and student.level:
        legacy_level = AcademicLevel.query.filter_by(name=student.level).first()
        student_level_id = legacy_level.id if legacy_level else None
    subject_id = request.args.get("subject_id", type=int)
    subject = db.session.get(Subject, subject_id) if subject_id else None
    mapped_subject_ids = {
        row.legacy_subject_id
        for row in AcademicYearSubject.query.filter_by(
            academic_year_id=exam.academic_year_id,
            academic_year_level_id=enrollment.academic_year_level_id,
            is_active=True,
        ).all()
        if enrollment and row.legacy_subject_id
    } if enrollment else set()
    if not subject and request.args.get("subject") and student_level_id:
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
    if not mapped_subject_ids and (not student_level_id or subject.academic_level_id != student_level_id):
        return jsonify(ok=False, message="Maaddadani kuma jirto heerka ardeygan."), 404

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
    student = Student.query.filter(func.trim(Student.student_code) == student_code.strip()).first()
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

    settings = get_settings()
    students = top_students_for_class(student, exam)
    for entry in students:
        entry["photo"] = _public_asset_url(entry.pop("photo_path", "")) or _public_asset_url(settings.get("result_dashboard_default_avatar"))
    class_name = students[0]["class_name"] if students else (
        student.academic_class.name if student.academic_class else student.level or "Class"
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
    status = "Expired" if issue.expiry_date and issue.expiry_date < date.today() else issue.status
    
    # Debug logging - Student details
    logger.info(f"VERIFY STUDENT - Student ID: {issue.student.id}, Student Code: {issue.student.student_code}")
    logger.info(f"VERIFY STUDENT - Student academic_year_id: {issue.student.academic_year_id}")
    logger.info(f"VERIFY STUDENT - Student academic_level_id: {issue.student.academic_level_id}")
    logger.info(f"VERIFY STUDENT - Student academic_class_id: {issue.student.academic_class_id}")
    logger.info(f"VERIFY STUDENT - Student academic_section_id: {issue.student.academic_section_id}")
    
    exam = active_exam_for_student(issue.student, preferred_year_id=issue.academic_year_id)
    
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
    
    return render_template("verify_id.html", settings=settings, verified=True, issue=issue, display_status=status, exam=exam)


@public_bp.route("/qr/<token>")
def qr_landing(token):
    """QR Landing Page with two action cards"""
    settings = get_settings()
    issue = IdCardIssue.query.filter_by(token=token).first()
    if not issue:
        return render_template("qr_landing.html", settings=settings, token=token, student=None), 404
    return render_template("qr_landing.html", settings=settings, token=token, student=issue.student)


@public_bp.route("/incident-report/<token>", methods=["GET", "POST"])
def incident_report_form(token):
    """Incident Report Form - Requires invigilator authentication"""
    from .routes_invigilator import current_invigilator, invigilator_login_required
    
    settings = get_settings()
    issue = IdCardIssue.query.filter_by(token=token).first()
    
    if not issue:
        return render_template("qr_landing.html", settings=settings, token=token, student=None), 404
    
    student = issue.student
    student_subjects = incident_subjects_for_student(student, issue.academic_year_id)
    student_subject_ids = {subject.id for subject in student_subjects}
    
    # Debug logging - Student details
    import logging
    logger = logging.getLogger(__name__)
    logger.info(f"INCIDENT REPORT - Student ID: {student.id}, Student Code: {student.student_code}")
    logger.info(f"INCIDENT REPORT - Student academic_year_id: {student.academic_year_id}")
    logger.info(f"INCIDENT REPORT - Student academic_level_id: {student.academic_level_id}")
    logger.info(f"INCIDENT REPORT - Student academic_class_id: {student.academic_class_id}")
    logger.info(f"INCIDENT REPORT - Student academic_section_id: {student.academic_section_id}")
    
    exam = active_exam_for_student(student, preferred_year_id=issue.academic_year_id)
    
    if exam:
        # Debug logging - Exam details
        logger.info(f"INCIDENT REPORT - Exam found: ID={exam.id}, Name={exam.name}")
        logger.info(f"INCIDENT REPORT - Exam academic_year_id: {exam.academic_year_id}")
        logger.info(f"INCIDENT REPORT - Exam academic_level_id: {exam.academic_level_id}")
        logger.info(f"INCIDENT REPORT - Exam academic_class_id: {exam.academic_class_id}")
        logger.info(f"INCIDENT REPORT - Exam academic_section_id: {exam.academic_section_id}")
        logger.info(f"INCIDENT REPORT - Exam is_active: {exam.is_active}")
        logger.info(f"INCIDENT REPORT - Exam is_published: {exam.is_published}")
    else:
        logger.warning(f"INCIDENT REPORT - No exam found through shared active exam lookup")
        # Log all exams for this academic year for debugging
        issue_year_exams = Exam.query.filter_by(academic_year_id=issue.academic_year_id).all()
        student_year_exams = Exam.query.filter_by(academic_year_id=student.academic_year_id).all()
        logger.info(f"INCIDENT REPORT - ID card year exams: {[(e.id, e.name, e.is_active, e.is_published, e.academic_level_id, e.academic_class_id, e.academic_section_id) for e in issue_year_exams]}")
        logger.info(f"INCIDENT REPORT - Student year exams: {[(e.id, e.name, e.is_active, e.is_published, e.academic_level_id, e.academic_class_id, e.academic_section_id) for e in student_year_exams]}")
    
    # Check if invigilator is logged in
    invigilator = current_invigilator()
    if not invigilator:
        from flask import session
        session["invigilator_next"] = request.url
        return redirect(url_for("invigilator.login"))

    from .models import IncidentReportSettings
    settings_dict = {
        setting.setting_key: setting.setting_value
        for setting in IncidentReportSettings.query.all()
    }
    allow_signature_reuse = incident_bool_setting(settings_dict, "allow_signature_reuse", True)
    
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
            validation_errors.append("Please select a Category.")
        if incident_bool_setting(settings_dict, "require_severity", True) and not severity_id:
            validation_errors.append("Please select a Severity Level.")
        if incident_bool_setting(settings_dict, "require_description", True) and not description:
            validation_errors.append("Description is required.")
        if incident_bool_setting(settings_dict, "require_signature", False) and not signature_data:
            validation_errors.append("Signature is required.")
        if incident_bool_setting(settings_dict, "require_evidence", False) and not evidence_files:
            validation_errors.append("Please upload evidence.")
        if incident_bool_setting(settings_dict, "require_subject", False) and not request.form.get("subject_id"):
            validation_errors.append("Please select a Subject.")
        if incident_bool_setting(settings_dict, "require_actions_taken", False) and not actions_list:
            validation_errors.append("Please select at least one Action Taken.")
        if incident_bool_setting(settings_dict, "require_incident_date", True) and not request.form.get("incident_date"):
            validation_errors.append("Incident Date is required.")
        if incident_bool_setting(settings_dict, "require_incident_time", True) and not request.form.get("incident_time"):
            validation_errors.append("Incident Time is required.")
        if validation_errors:
            return incident_form_error("Please correct the highlighted fields.", validation_errors)

        subject_id = request.form.get("subject_id", type=int)
        if subject_id and subject_id not in student_subject_ids:
            return incident_form_error("Please select a subject assigned to this student's level.")

        if not category_ids:
            default_category = IncidentCategory.query.order_by(IncidentCategory.sort_order, IncidentCategory.id).first()
            category_ids = [default_category.id] if default_category else []
        if not severity_id:
            default_severity = SeverityLevel.query.order_by(SeverityLevel.sort_order, SeverityLevel.id).first()
            severity_id = default_severity.id if default_severity else None
        if not category_ids or not severity_id:
            return incident_form_error("Incident categories and severity levels must be configured before submitting reports.")
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
            return incident_form_error("Please select a valid incident category.")
        if not severity:
            return incident_form_error("Please select a valid severity level.")

        category_is_other = any(is_other_lookup_value(category.name) for category in selected_categories)
        action_has_other = any(is_other_lookup_value(action) for action in actions_list)

        if category_is_other and not category_description and not other_description:
            return incident_form_error("Please describe the specific Category details.")
        if action_has_other and not action_description and not other_description:
            return incident_form_error("Please describe the specific Action details.")

        if len(category_description) > 500:
            return incident_form_error("Category description must be 500 characters or fewer.")
        if len(action_description) > 500:
            return incident_form_error("Action description must be 500 characters or fewer.")
        if len(other_description) > 500:
            return incident_form_error("The description must be 500 characters or fewer.")

        # Legacy fallback if old form submitted single other_description
        if not category_description and category_is_other and other_description:
            category_description = other_description
        if not action_description and action_has_other and other_description:
            action_description = other_description

        # Combined fallback for other_description field for legacy queries
        legacy_other_combined = other_description or (" / ".join(filter(None, [category_description, action_description]))) or None

        if incident_bool_setting(settings_dict, "require_exam", False) and not exam:
            return incident_form_error("No active exam found for this student.")
        try:
            incident_date = parse_incident_date(request.form.get("incident_date"))
            incident_time = parse_incident_time(request.form.get("incident_time"))
        except ValueError:
            return incident_form_error("Please enter a valid incident date and time.")
        
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
            return incident_form_error("Unable to save the report. Please try again.", status=500)
        
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
    exams = Exam.query.filter_by(is_published=True).order_by(Exam.id.desc()).all()
    subjects = student_subjects
    
    # Pre-compute current date/time for form defaults
    now = datetime.now()
    current_date = now.strftime('%B %d, %Y')
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
        exams=exams,
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
