import secrets
import calendar
from datetime import date
from io import BytesIO
from tempfile import NamedTemporaryFile
from pathlib import Path
from urllib.request import Request, urlopen

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, send_file, url_for
from flask_login import login_required
from sqlalchemy import and_, func, or_
from sqlalchemy.orm import joinedload

from . import db
from .audit import audit
from .models import AcademicClass, AcademicLevel, AcademicSection, AcademicYear, AcademicYearClass, AcademicYearLevel, Exam, IdCardIssue, SchoolClass, Setting, Student, StudentEnrollment
from .permissions import enforce_endpoint_permission
from .enrollment_service import EnrollmentValidationError, student_enrollment_legacy_scope_query, student_enrollment_scope_query
from .services import get_settings
from .verification import id_card_qr_payload

id_cards_bp = Blueprint("admin_id_cards", __name__)

ID_CARD_TEMPLATES = {
    "classic": {"primary": "#002060", "accent": "#007bff", "background": "#ffffff", "font": "Segoe UI", "border": "solid", "rounded": "on"},
    "modern": {"primary": "#0f766e", "accent": "#14b8a6", "background": "#f8fffd", "font": "Segoe UI", "border": "minimal", "rounded": "on"},
    "premium": {"primary": "#111827", "accent": "#d4af37", "background": "#fffdf5", "font": "Georgia", "border": "double", "rounded": "on"},
    "minimal": {"primary": "#334155", "accent": "#64748b", "background": "#ffffff", "font": "Arial", "border": "minimal", "rounded": "off"},
    "executive": {"primary": "#312e81", "accent": "#7c3aed", "background": "#fbfaff", "font": "Tahoma", "border": "solid", "rounded": "on"},
}


@id_cards_bp.before_request
@login_required
def require_login():
    enforce_endpoint_permission()


@id_cards_bp.route("/", methods=["GET", "POST"])
def dashboard():
    if request.method == "POST":
        template_name = request.form.get("template_name", "").strip().lower()
        if request.form.get("action") == "apply_template" and template_name in ID_CARD_TEMPLATES:
            apply_template(template_name)
            audit("Settings Changes", f"Applied ID card template {template_name}")
            db.session.commit()
            flash(f"{template_name.title()} template applied.", "success")
            return redirect(url_for("admin_id_cards.dashboard"))
        editable_keys = [
            "id_card_size", "id_card_orientation", "id_card_background", "id_card_primary_color",
            "id_card_accent_color", "id_card_font_family", "id_card_border_style", "id_card_rounded_corners",
            "id_card_template", "id_card_logo_position", "id_card_photo_position", "id_card_qr_position",
            "id_card_icon_style", "id_card_label_style", "id_card_spacing", "id_card_print_margin",
            "id_card_show_barcode", "id_card_footer", "id_card_watermark", "id_card_issue_months",
            "id_card_office_signature", "id_card_stamp_text", "id_card_found_contact_text", "id_card_exam_type",
            "id_card_header_text", "id_card_signature_text",
        ]
        raw_issue_months = request.form.get("id_card_issue_months", "12").strip()
        try:
            issue_months = int(raw_issue_months)
        except (TypeError, ValueError):
            issue_months = 0
        if not 1 <= issue_months <= 120:
            flash("Valid Months waa inuu noqdaa tiro u dhexeysa 1 iyo 120 bilood.", "danger")
            return redirect(url_for("admin_id_cards.dashboard"))
        for key in editable_keys:
            setting = db.session.get(Setting, key) or Setting(key=key)
            setting.value = str(issue_months) if key == "id_card_issue_months" else request.form.get(key, "").strip()
            db.session.add(setting)
        audit("Settings Changes", "Updated ID card designer settings")
        db.session.commit()
        flash("ID card designer saved.", "success")
        return redirect(url_for("admin_id_cards.dashboard"))

    filters = card_filters()
    exams = (
        Exam.query
        .join(AcademicYear, AcademicYear.id == Exam.academic_year_id)
        .filter(Exam.is_active.is_(True))
        .order_by(AcademicYear.name.desc(), Exam.sort_order, Exam.name, Exam.id)
        .all()
    )
    selected_exam = db.session.get(Exam, filters["exam_id"]) if filters["exam_id"] else None
    if selected_exam and (
        not selected_exam.is_active
        or (filters["year_id"] and selected_exam.academic_year_id != filters["year_id"])
    ):
        selected_exam = None

    scope_filters = dict(filters)
    option_filters = dict(filters)
    if selected_exam:
        scope_filters["year_id"] = selected_exam.academic_year_id
        option_filters["year_id"] = selected_exam.academic_year_id
        students = (
            filter_students_for_exam(filtered_students(scope_filters), selected_exam)
            .order_by(Student.full_name, Student.id)
            .limit(500)
            .all()
        )
    else:
        students = []
    placements_by_scope = resolve_student_placements(
        (student, selected_exam.academic_year_id)
        for student in students
    ) if selected_exam else {}
    student_placements = {
        student.id: placements_by_scope.get((student.id, selected_exam.academic_year_id), {})
        for student in students
    } if selected_exam else {}
    students = [student for student in students if student_placements.get(student.id)]
    issues = (
        IdCardIssue.query
        .options(joinedload(IdCardIssue.student), joinedload(IdCardIssue.exam))
        .order_by(IdCardIssue.updated_at.desc())
        .limit(200)
        .all()
    )
    issue_dates_changed = sync_issue_dates(issues, settings=get_settings())
    if issue_dates_changed:
        db.session.commit()
    issue_placements_by_scope = resolve_student_placements(
        (issue.student, issue.academic_year_id)
        for issue in issues
        if issue.student
    )
    issue_placements = {
        issue.id: issue_placements_by_scope.get((issue.student_id, issue.academic_year_id), {})
        for issue in issues
    }
    issue_statuses = {issue.id: effective_issue_status(issue) for issue in issues}
    return render_template(
        "admin/id_cards.html",
        settings=get_settings(),
        students=students,
        student_placements=student_placements,
        issues=issues,
        issue_placements=issue_placements,
        issue_statuses=issue_statuses,
        filters=filters,
        selected_exam=selected_exam,
        classes=filter_class_options(option_filters),
        years=AcademicYear.query.order_by(AcademicYear.name.desc()).all(),
        levels=filter_level_options(option_filters),
        sections=filter_section_options(option_filters),
        exams=exams,
        templates=ID_CARD_TEMPLATES,
    )


def apply_template(template_name):
    preset = ID_CARD_TEMPLATES[template_name]
    values = {
        "id_card_template": template_name,
        "id_card_primary_color": preset["primary"],
        "id_card_accent_color": preset["accent"],
        "id_card_background": preset["background"],
        "id_card_font_family": preset["font"],
        "id_card_border_style": preset["border"],
        "id_card_rounded_corners": preset["rounded"],
    }
    for key, value in values.items():
        setting = db.session.get(Setting, key) or Setting(key=key)
        setting.value = value
        db.session.add(setting)


@id_cards_bp.route("/generate/<int:student_id>", methods=["POST"])
def generate(student_id):
    student = db.session.get(Student, student_id) or abort(404)
    academic_year_id = int_or_none(request.form.get("academic_year_id"))
    exam_id = int_or_none(request.form.get("exam_id"))
    try:
        issue = get_or_create_issue(student, academic_year_id=academic_year_id, exam_id=exam_id)
    except EnrollmentValidationError as exc:
        db.session.rollback()
        flash(str(exc), "warning")
        return redirect(url_for("admin_id_cards.dashboard"))
    audit("ID Card Operations", f"Generated ID card for {student.student_code}")
    db.session.commit()
    flash("ID card generated.", "success")
    return redirect(url_for("admin_id_cards.print_cards", issue_ids=issue.id))


@id_cards_bp.route("/bulk-generate", methods=["POST"])
def bulk_generate():
    scope = request.form.get("scope", "selected")
    single_student_id = int_or_none(request.form.get("single_student_id"))
    requested_ids = list(dict.fromkeys(
        int(value) for value in request.form.getlist("student_ids") if value.isdigit()
    ))
    academic_year_id = int_or_none(request.form.get("academic_year_id"))
    exam_id = int_or_none(request.form.get("exam_id"))
    exam = db.session.get(Exam, exam_id) if exam_id else None
    if not exam or not exam.is_active or exam.academic_year_id != academic_year_id:
        flash("Dooro Exam Type firfircoon oo sannadkiisa la jaanqaadaya.", "warning")
        return redirect(url_for("admin_id_cards.dashboard"))

    scope_filters = {
        "q": request.form.get("q", "").strip(),
        "class_id": None,
        "academic_year_class_id": int_or_none(request.form.get("academic_year_class_id")),
        "year_id": academic_year_id,
        "level": request.form.get("level", "").strip(),
        "section": request.form.get("section", "").strip(),
    }
    eligible_query = filter_students_for_exam(filtered_students(scope_filters), exam)

    if single_student_id:
        ids = [single_student_id] if eligible_query.filter(Student.id == single_student_id).first() else []
    elif scope == "filtered":
        ids = [student_id for student_id, in eligible_query.with_entities(Student.id).order_by(Student.full_name, Student.id).all()]
    else:
        eligible_ids = {
            student_id
            for student_id, in eligible_query
            .filter(Student.id.in_(requested_ids))
            .with_entities(Student.id)
            .all()
        } if requested_ids else set()
        ids = [student_id for student_id in requested_ids if student_id in eligible_ids]

    if not ids:
        flash("Arday ku habboon ma helin. Hubi filters-ka ama dooro ardayda.", "warning")
        return redirect(url_for("admin_id_cards.dashboard"))

    students_by_id = {
        student.id: student
        for student in Student.query.filter(Student.id.in_(ids)).all()
    }
    issue_ids = []
    try:
        for student_id in ids:
            student = students_by_id[student_id]
            issue_ids.append(get_or_create_issue(student, academic_year_id=academic_year_id, exam_id=exam_id).id)
    except EnrollmentValidationError as exc:
        db.session.rollback()
        flash(str(exc), "warning")
        return redirect(url_for("admin_id_cards.dashboard"))
    operation = "Generated ID card" if single_student_id else f"Bulk generated {len(issue_ids)} ID cards"
    audit("ID Card Operations", f"{operation} for {len(issue_ids)} student(s) using exam {exam.id}")
    db.session.commit()
    flash(f"Waxaa la diyaariyey {len(issue_ids)} kaadh.", "success")
    return_filters = {
        "back_q": request.form.get("q", "").strip(),
        "back_year_id": request.form.get("return_year_id", ""),
        "back_exam_id": request.form.get("return_exam_id", ""),
        "back_level": request.form.get("level", "").strip(),
        "back_class_id": request.form.get("academic_year_class_id", ""),
        "back_section": request.form.get("section", "").strip(),
    }
    return redirect(url_for(
        "admin_id_cards.print_cards",
        issue_ids=",".join(str(i) for i in issue_ids),
        **return_filters,
    ))


@id_cards_bp.route("/print")
def print_cards():
    issues = selected_issues()
    settings = get_settings()
    if sync_issue_dates(issues, settings=settings):
        db.session.commit()
    placements_by_scope = resolve_student_placements(
        (issue.student, issue.academic_year_id)
        for issue in issues
        if issue.student
    )
    cards = [
        {
            "issue": issue,
            "placement": placements_by_scope.get((issue.student_id, issue.academic_year_id), {}),
            "qr": id_card_qr_payload(issue),
        }
        for issue in issues
    ]
    return_filter_args = {
        "q": "back_q",
        "year_id": "back_year_id",
        "exam_id": "back_exam_id",
        "level": "back_level",
        "academic_year_class_id": "back_class_id",
        "section": "back_section",
    }
    return_url_args = {
        target: request.args[source]
        for target, source in return_filter_args.items()
        if source in request.args
    }
    return_url = url_for("admin_id_cards.dashboard", **return_url_args)
    return render_template(
        "admin/id_card_print.html",
        cards=cards,
        settings=settings,
        return_url=return_url,
    )


@id_cards_bp.route("/export.pdf")
def export_pdf():
    issues = selected_issues()
    from io import BytesIO
    import qrcode
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    tmp = NamedTemporaryFile(delete=False, suffix=".pdf")
    pdf = canvas.Canvas(tmp.name, pagesize=A4)
    settings = get_settings()
    if sync_issue_dates(issues, settings=settings):
        db.session.commit()
    page_w, page_h = A4
    margin = max(5, min(float(settings.get("id_card_print_margin") or 8), 15)) * mm
    gap = 5 * mm
    slot_w = (page_w - (2 * margin) - gap) / 2
    slot_h = (page_h - (2 * margin) - gap) / 2
    primary = colors.HexColor(settings.get("id_card_primary_color") or "#002060")
    accent = colors.HexColor(settings.get("id_card_accent_color") or "#007bff")
    template = ID_CARD_TEMPLATES.get((settings.get("id_card_template") or "classic").lower())
    if template:
        primary = colors.HexColor(template["primary"])
        accent = colors.HexColor(template["accent"])
    for index, issue in enumerate(issues):
        slot = index % 4
        if index and slot == 0:
            pdf.showPage()
        col = slot % 2
        row = slot // 2
        x = margin + col * (slot_w + gap)
        y = page_h - margin - (row + 1) * slot_h - row * gap
        draw_id_card_pdf(pdf, issue, settings, x, y, slot_w, slot_h, primary, accent, qrcode, ImageReader, BytesIO)
    if not issues:
        pdf.drawString(margin, page_h - margin, "No ID cards selected.")
    pdf.save()
    audit("ID Card Operations", "Exported ID cards PDF")
    db.session.commit()
    return send_file(tmp.name, as_attachment=True, download_name="student_id_cards.pdf")


def draw_id_card_pdf(pdf, issue, settings, x, y, w, h, primary, accent, qrcode, ImageReader, BytesIO):
    from reportlab.lib import colors
    from reportlab.lib.units import mm

    pdf.setDash(3, 2)
    pdf.setStrokeColor(colors.HexColor("#94a3b8"))
    pdf.rect(x, y, w, h, stroke=1, fill=0)
    pdf.setDash()
    pad = 3 * mm
    card_x, card_y = x + pad, y + pad
    card_w, card_h = w - 2 * pad, h - 2 * pad
    pdf.setStrokeColor(primary)
    pdf.setFillColor(colors.HexColor(settings.get("id_card_background") or "#ffffff"))
    pdf.roundRect(card_x, card_y, card_w, card_h, 8, stroke=1, fill=1)

    header_h = 22 * mm
    pdf.setFillColor(primary)
    pdf.roundRect(card_x, card_y + card_h - header_h, card_w, header_h, 8, stroke=0, fill=1)
    pdf.setFillColor(colors.white)
    logo_x, logo_y = card_x + 4 * mm, card_y + card_h - header_h + 4 * mm
    pdf.roundRect(logo_x, logo_y, 14 * mm, 14 * mm, 5, stroke=0, fill=1)
    if settings.get("logo_path"):
        logo_image = reportlab_image_source(settings.get("logo_path"), ImageReader, BytesIO)
        if logo_image:
            pdf.drawImage(logo_image, logo_x + 1, logo_y + 1, 14 * mm - 2, 14 * mm - 2, preserveAspectRatio=True, mask="auto")
        else:
            pdf.setFillColor(primary)
            pdf.setFont("Helvetica-Bold", 7)
            pdf.drawCentredString(logo_x + 7 * mm, logo_y + 6 * mm, "LOGO")
    else:
        pdf.setFillColor(primary)
        pdf.setFont("Helvetica-Bold", 7)
        pdf.drawCentredString(logo_x + 7 * mm, logo_y + 6 * mm, "LOGO")

    pdf.setFillColor(colors.white)
    pdf.setFont("Helvetica-Bold", 10)
    pdf.drawString(card_x + 21 * mm, card_y + card_h - 8 * mm, (settings.get("school_name") or "School")[:36])
    pdf.setFont("Helvetica-Bold", 7)
    pdf.drawString(card_x + 21 * mm, card_y + card_h - 14 * mm, f"Academic Year: {issue.academic_year.name[:18]}")
    pdf.setFont("Helvetica-Bold", 6)
    exam_name = issue.exam.name if issue.exam else (settings.get("id_card_exam_type") or "")
    if exam_name:
        pdf.drawString(card_x + 21 * mm, card_y + card_h - 19 * mm, f"Exam: {exam_name[:28]}")

    title_y = card_y + card_h - header_h - 8 * mm
    pdf.setFillColor(accent)
    pdf.setFont("Helvetica-Bold", 10)
    pdf.drawCentredString(card_x + card_w / 2, title_y, settings.get("id_card_header_text") or "KAARKA OGOLAANSHAHA IMTIXAANKA")

    body_y = card_y + 16 * mm
    photo_size_map = {"small": 25, "medium": 29, "large": 33}
    photo_size = photo_size_map.get(settings.get("student_photo_size") or "medium", 29) * mm
    photo_radius = 7 if settings.get("student_photo_shape") == "rounded" else photo_size / 2
    photo_x = card_x + 5 * mm
    photo_y = body_y + 30 * mm
    pdf.setStrokeColor(accent)
    pdf.setFillColor(colors.HexColor("#eef6ff"))
    pdf.roundRect(photo_x, photo_y, photo_size, photo_size, photo_radius, stroke=1 if settings.get("student_photo_border") == "on" else 0, fill=1)
    if issue.student.photo_path:
        photo_image = reportlab_image_source(issue.student.photo_path, ImageReader, BytesIO)
        if photo_image:
            pdf.drawImage(photo_image, photo_x + 1, photo_y + 1, photo_size - 2, photo_size - 2, preserveAspectRatio=True, mask="auto")
    pdf.setFillColor(primary)
    pdf.setFont("Helvetica-Bold", 7)
    pdf.drawCentredString(photo_x + 14.5 * mm, photo_y - 4 * mm, (issue.student.phone or "-")[:18])

    info_x = photo_x + 34 * mm
    pdf.setFillColor(primary)
    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawString(info_x, body_y + 60 * mm, issue.student.full_name[:36])
    pdf.setFont("Helvetica", 6)
    pdf.setFillColor(colors.HexColor("#64748b"))
    pdf.drawString(info_x, body_y + 56.5 * mm, "STUDENT NAME")
    pdf.setFillColor(primary)
    pdf.setFont("Helvetica-Bold", 8)
    pdf.drawString(info_x, body_y + 47 * mm, f"ID Number: {issue.student.student_code}")
    pdf.drawString(info_x, body_y + 38 * mm, f"Mother: {(issue.student.mother_name or '-')[:28]}")
    pdf.drawString(info_x, body_y + 28 * mm, f"Issue Date: {issue.issue_date}")
    pdf.drawString(info_x, body_y + 20 * mm, f"Expiry Date: {issue.expiry_date or ''}")

    qr = qrcode.make(id_card_qr_payload(issue)["url"])
    buffer = BytesIO()
    qr.save(buffer, format="PNG")
    buffer.seek(0)
    qr_size = 23 * mm
    qr_x = card_x + card_w - qr_size - 8 * mm
    qr_y = body_y - 1 * mm
    pdf.setFillColor(colors.HexColor("#f8fbff"))
    pdf.roundRect(qr_x - 2 * mm, qr_y - 2 * mm, qr_size + 4 * mm, qr_size + 6 * mm, 7, stroke=1, fill=1)
    pdf.drawImage(ImageReader(buffer), qr_x, qr_y, qr_size, qr_size)
    pdf.setFont("Helvetica-Bold", 5.5)
    pdf.setFillColor(primary)
    pdf.drawCentredString(qr_x + qr_size / 2, qr_y - 1 * mm, "SCAN TO VERIFY")

    footer_h = 12 * mm
    pdf.setFillColor(primary)
    pdf.rect(card_x, card_y, card_w, footer_h, stroke=0, fill=1)
    pdf.setFillColor(colors.white)
    pdf.setFont("Helvetica-Bold", 7)
    contact = f"{settings.get('id_card_footer') or settings.get('id_card_found_contact_text')} {settings.get('school_phone') or settings.get('call_url') or ''}"
    pdf.drawCentredString(card_x + card_w / 2, card_y + 4.5 * mm, contact[:95])


def get_or_create_issue(student, academic_year_id=None, exam_id=None):
    """Get/create an ID card in one year + exam scope."""
    year_id = resolve_id_card_year_id(student, academic_year_id)
    exam = db.session.get(Exam, exam_id) if exam_id else None
    if not exam:
        raise EnrollmentValidationError("Dooro Exam Type ka hor intaadan sameyn ID Card.")
    if exam.academic_year_id != year_id or not exam.is_active:
        raise EnrollmentValidationError("Exam Type-ka la doortay kuma xirna sannadka ardayga.")
    if not student.is_active or not filter_students_for_exam(
        student_enrollment_scope_query(year_id), exam
    ).filter(Student.id == student.id).first():
        raise EnrollmentValidationError("Ardaygani kuma jiro baaxadda Exam Type-ka la doortay.")
    issue = IdCardIssue.query.filter_by(
        student_id=student.id,
        academic_year_id=year_id,
        exam_id=exam.id,
    ).first()
    settings = get_settings()
    months = configured_issue_months(settings)
    if issue:
        ensure_issue_dates(issue, months=months)
        if issue.status == "Blocked":
            raise EnrollmentValidationError("Kaadhkan waa la xannibay; maamulka ha dib u eego ka hor dib-u-soo-saar.")
        if effective_issue_status(issue) != "Active":
            issue.token = secrets.token_urlsafe(32)
            issue.issue_date = date.today()
            issue.expiry_date = add_months(date.today(), months)
            issue.status = "Active"
        return issue

    issue = IdCardIssue(
        token=secrets.token_urlsafe(32),
        student=student,
        academic_year_id=year_id,
        exam_id=exam.id,
        issue_date=date.today(),
        expiry_date=add_months(date.today(), months),
        status="Active",
    )
    db.session.add(issue)
    db.session.flush()
    return issue


def configured_issue_months(settings=None):
    """Return a safe validity period for newly issued and repaired cards."""
    if settings is None:
        settings = get_settings()
    try:
        value = int(str(settings.get("id_card_issue_months") or "12").strip())
    except (TypeError, ValueError):
        value = 12
    return max(1, min(value, 120))


def ensure_issue_dates(issue, *, settings=None, months=None, today=None):
    """Repair missing/invalid dates and mark an active expired card."""
    if not issue:
        return False
    today = today or date.today()
    if months is None:
        months = configured_issue_months(settings)
    changed = False
    if not issue.issue_date:
        issue.issue_date = issue.created_at.date() if issue.created_at else today
        changed = True
    if not issue.expiry_date or issue.expiry_date < issue.issue_date:
        issue.expiry_date = add_months(issue.issue_date, months)
        changed = True
    if issue.status == "Active" and issue.expiry_date < today:
        issue.status = "Expired"
        changed = True
    return changed


def sync_issue_dates(issues, *, settings=None):
    """Apply date repair and expiry transitions to every issue in a collection."""
    changed = False
    for issue in issues:
        if ensure_issue_dates(issue, settings=settings):
            changed = True
    return changed


def resolve_id_card_year_id(student, academic_year_id=None):
    """Resolve the required year from the selected scope or authoritative enrollment.

    ``Student.academic_year_id`` is a legacy snapshot and may be empty after
    enrollment migration. ID cards still require a concrete year, so never
    allow a missing value to reach the non-null ``id_card_issues`` column.
    """
    if academic_year_id:
        year = db.session.get(AcademicYear, academic_year_id)
        if not year:
            raise EnrollmentValidationError("The selected academic year does not exist.")
        from .enrollment_service import resolve_student_academic_context

        if not resolve_student_academic_context(student, year.id) and student.academic_year_id != year.id:
            raise EnrollmentValidationError(
                f"Ardayga {student.student_code} kuma jiro sannadka la doortay."
            )
        return year.id

    enrollment = (
        StudentEnrollment.query
        .filter_by(student_id=student.id)
        .join(AcademicYear, StudentEnrollment.academic_year_id == AcademicYear.id)
        .order_by(
            AcademicYear.is_current.desc(),
            StudentEnrollment.enrolled_at.desc(),
            StudentEnrollment.academic_year_id.desc(),
        )
        .first()
    )
    if enrollment:
        return enrollment.academic_year_id

    if student.academic_year_id:
        year = db.session.get(AcademicYear, student.academic_year_id)
        if year:
            return year.id

    raise EnrollmentValidationError(
        f"ID card lama abuuri karo: ardayga {student.student_code} kuma xirna sannad dugsiyeed sax ah."
    )


def resolve_issue_placement(issue):
    """Return the issue-year placement, never a newer legacy snapshot."""
    return resolve_student_placements([(issue.student, issue.academic_year_id)]).get(
        (issue.student_id, issue.academic_year_id), {}
    )


def effective_issue_status(issue):
    """Return the status a user should see after applying expiry rules."""
    if issue.expiry_date and issue.expiry_date < date.today() and issue.status == "Active":
        return "Expired"
    return issue.status


def filter_level_options(filters):
    if filters["year_id"]:
        return [
            name
            for name, in db.session.query(AcademicYearLevel.name)
            .filter_by(academic_year_id=filters["year_id"], is_active=True)
            .group_by(AcademicYearLevel.name)
            .order_by(func.min(AcademicYearLevel.sort_order), AcademicYearLevel.name)
            .all()
        ]
    return [
        name
        for name, in db.session.query(AcademicYearLevel.name)
        .filter_by(is_active=True)
        .distinct()
        .order_by(AcademicYearLevel.name)
        .all()
    ]


def filter_section_options(filters):
    if filters["year_id"]:
        return [
            name
            for name, in db.session.query(AcademicSection.name)
            .join(AcademicYearClass, AcademicYearClass.legacy_class_id == AcademicSection.academic_class_id)
            .join(AcademicYearLevel, AcademicYearLevel.id == AcademicYearClass.academic_year_level_id)
            .filter(
                AcademicYearLevel.academic_year_id == filters["year_id"],
                AcademicSection.is_active.is_(True),
            )
            .group_by(AcademicSection.name)
            .order_by(func.min(AcademicSection.sort_order), AcademicSection.name)
            .all()
        ]
    return [
        name
        for name, in db.session.query(AcademicSection.name)
        .join(AcademicYearClass, AcademicYearClass.legacy_class_id == AcademicSection.academic_class_id)
        .join(AcademicYearLevel, AcademicYearLevel.id == AcademicYearClass.academic_year_level_id)
        .filter(AcademicSection.is_active.is_(True), AcademicYearLevel.is_active.is_(True))
        .distinct()
        .order_by(AcademicSection.name)
        .all()
    ]


def reportlab_image_source(path, image_reader, bytes_io):
    """Return a ReportLab image source for local or Cloudinary assets."""
    if not path:
        return None
    value = str(path)
    try:
        if value.startswith(("http://", "https://")):
            request = Request(value, headers={"User-Agent": "SULTAAN-EMS ID card renderer"})
            with urlopen(request, timeout=8) as response:
                return image_reader(bytes_io(response.read()))
        relative = value.removeprefix("/static/").lstrip("/")
        local_path = Path(current_app.static_folder) / relative
        if local_path.is_file():
            return image_reader(str(local_path))
    except Exception as exc:
        current_app.logger.warning("Unable to load ID card asset %s: %s", value, exc)
    return None


def selected_issues():
    raw = request.args.get("issue_ids", "")
    ids = [int(value) for value in raw.split(",") if value.isdigit()]
    query = IdCardIssue.query.options(
        joinedload(IdCardIssue.student),
        joinedload(IdCardIssue.exam),
    )
    if ids:
        return query.filter(IdCardIssue.id.in_(ids)).order_by(IdCardIssue.id).all()
    return query.order_by(IdCardIssue.updated_at.desc()).limit(50).all()


def card_filters():
    raw_year_id = request.args.get("year_id")
    year_id = int_or_none(raw_year_id)
    if raw_year_id is None:
        current_year = AcademicYear.query.filter_by(is_current=True).first()
        year_id = current_year.id if current_year else None
    return {
        "q": request.args.get("q", "").strip(),
        "class_id": int_or_none(request.args.get("class_id")),
        "academic_year_class_id": int_or_none(request.args.get("academic_year_class_id")),
        "year_id": year_id,
        "exam_id": int_or_none(request.args.get("exam_id")),
        "level": request.args.get("level", "").strip(),
        "section": request.args.get("section", "").strip(),
    }


def filtered_students(filters):
    query = Student.query.filter(Student.is_active.is_(True))
    if filters["year_id"]:
        if filters["academic_year_class_id"]:
            try:
                year_class = db.session.get(AcademicYearClass, filters["academic_year_class_id"])
                if (
                    not year_class
                    or year_class.academic_year_level.academic_year_id != filters["year_id"]
                    or (filters["level"] and year_class.academic_year_level.name != filters["level"])
                ):
                    raise EnrollmentValidationError("Fasalku kuma jiro level-ka ama sannadka la doortay.")
                query = student_enrollment_scope_query(
                    filters["year_id"],
                    academic_year_class_id=filters["academic_year_class_id"],
                )
            except EnrollmentValidationError:
                query = student_enrollment_scope_query(filters["year_id"]).filter(Student.id == -1)
        else:
            query = student_enrollment_scope_query(filters["year_id"])
    if filters["q"]:
        q = f"%{filters['q']}%"
        query = query.filter(or_(Student.student_code.like(q), Student.full_name.like(q), Student.mother_name.like(q)))
    if filters["class_id"] and not filters["year_id"]:
        query = query.filter(Student.class_id == filters["class_id"])
    if filters["level"]:
        if not filters["year_id"]:
            query = query.filter(Student.level == filters["level"])
        else:
            year_levels = AcademicYearLevel.query.filter_by(
                academic_year_id=filters["year_id"],
                name=filters["level"],
            ).all()
            if not year_levels:
                return query.filter(Student.id == -1)
            level_ids = [row.id for row in year_levels]
            legacy_level_ids = [row.legacy_level_id for row in year_levels if row.legacy_level_id]
            level_filters = []
            if level_ids:
                level_filters.append(StudentEnrollment.academic_year_level_id.in_(level_ids))
            legacy_level_filters = [Student.level == filters["level"]]
            if legacy_level_ids:
                legacy_level_filters.append(Student.academic_level_id.in_(legacy_level_ids))
            query = query.filter(or_(
                *level_filters,
                and_(
                    StudentEnrollment.id.is_(None),
                    Student.academic_year_id == filters["year_id"],
                    or_(*legacy_level_filters),
                ),
            ))
    if filters["section"]:
        if filters["year_id"]:
            section_ids = [
                section_id
                for section_id, in db.session.query(AcademicSection.id)
                .join(AcademicYearClass, AcademicYearClass.legacy_class_id == AcademicSection.academic_class_id)
                .join(AcademicYearLevel, AcademicYearLevel.id == AcademicYearClass.academic_year_level_id)
                .filter(
                    AcademicSection.name == filters["section"],
                    AcademicYearLevel.academic_year_id == filters["year_id"],
                )
                .distinct()
                .all()
            ]
            legacy_section_filters = [Student.section == filters["section"]]
            if section_ids:
                legacy_section_filters.append(Student.academic_section_id.in_(section_ids))
            query = query.filter(or_(
                StudentEnrollment.academic_section_id.in_(section_ids) if section_ids else Student.id == -1,
                and_(
                    StudentEnrollment.id.is_(None),
                    Student.academic_year_id == filters["year_id"],
                    or_(*legacy_section_filters),
                ),
            ))
        else:
            query = query.filter(Student.section == filters["section"])
    return query


def filter_students_for_exam(query, exam):
    """Restrict an ID-card roster to the selected exam's configured scope."""
    year_id = exam.academic_year_id
    if exam.academic_level_id:
        year_levels = AcademicYearLevel.query.filter_by(
            academic_year_id=year_id,
            legacy_level_id=exam.academic_level_id,
        ).all()
        if not year_levels:
            legacy_level = db.session.get(AcademicLevel, exam.academic_level_id)
            if legacy_level:
                year_levels = AcademicYearLevel.query.filter_by(
                    academic_year_id=year_id,
                    name=legacy_level.name,
                ).all()
        level_ids = [item.id for item in year_levels]
        level_names = list(dict.fromkeys(item.name for item in year_levels))
        legacy_level = db.session.get(AcademicLevel, exam.academic_level_id)
        if legacy_level and legacy_level.name not in level_names:
            level_names.append(legacy_level.name)
        level_filters = []
        if level_ids:
            level_filters.append(StudentEnrollment.academic_year_level_id.in_(level_ids))
        level_filters.append(and_(
            StudentEnrollment.id.is_(None),
            Student.academic_year_id == year_id,
            Student.academic_level_id == exam.academic_level_id,
        ))
        if level_names:
            level_filters.append(and_(
                StudentEnrollment.id.is_(None),
                Student.academic_year_id == year_id,
                Student.level.in_(level_names),
            ))
        query = query.filter(or_(*level_filters))

    if exam.academic_class_id:
        year_classes = (
            AcademicYearClass.query
            .join(AcademicYearLevel, AcademicYearLevel.id == AcademicYearClass.academic_year_level_id)
            .filter(
                AcademicYearLevel.academic_year_id == year_id,
                AcademicYearClass.legacy_class_id == exam.academic_class_id,
            )
            .all()
        )
        legacy_class = db.session.get(AcademicClass, exam.academic_class_id)
        if not year_classes and legacy_class:
            year_classes = (
                AcademicYearClass.query
                .join(AcademicYearLevel, AcademicYearLevel.id == AcademicYearClass.academic_year_level_id)
                .filter(
                    AcademicYearLevel.academic_year_id == year_id,
                    AcademicYearClass.name == legacy_class.name,
                )
                .all()
            )
        class_ids = [item.id for item in year_classes]
        class_names = list(dict.fromkeys(item.name for item in year_classes))
        if legacy_class and legacy_class.name not in class_names:
            class_names.append(legacy_class.name)
        school_class_ids = [
            item.id for item in SchoolClass.query.filter(SchoolClass.name.in_(class_names)).all()
        ] if class_names else []
        class_filters = []
        if class_ids:
            class_filters.append(StudentEnrollment.academic_year_class_id.in_(class_ids))
        class_filters.append(and_(
            StudentEnrollment.id.is_(None),
            Student.academic_year_id == year_id,
            Student.academic_class_id == exam.academic_class_id,
        ))
        if school_class_ids:
            class_filters.append(and_(
                StudentEnrollment.id.is_(None),
                Student.academic_year_id == year_id,
                Student.class_id.in_(school_class_ids),
            ))
        query = query.filter(or_(*class_filters))

    if exam.academic_section_id:
        section = db.session.get(AcademicSection, exam.academic_section_id)
        if not section:
            return query.filter(Student.id == -1)
        query = query.filter(or_(
            StudentEnrollment.academic_section_id == section.id,
            and_(
                StudentEnrollment.id.is_(None),
                Student.academic_year_id == year_id,
                or_(Student.academic_section_id == section.id, Student.section == section.name),
            ),
        ))
    return query


def distinct_values(column):
    return [value[0] for value in db.session.query(column).filter(column.isnot(None), column != "").distinct().order_by(column).all()]


def filter_class_options(filters):
    """Return only classes configured under the active academic-year scope."""
    query = (
        AcademicYearClass.query
        .join(AcademicYearLevel, AcademicYearLevel.id == AcademicYearClass.academic_year_level_id)
        .filter(
            AcademicYearClass.is_active.is_(True),
            AcademicYearLevel.is_active.is_(True),
        )
        .order_by(AcademicYearClass.sort_order, AcademicYearClass.name, AcademicYearClass.id)
    )
    if filters["year_id"]:
        query = query.filter(AcademicYearLevel.academic_year_id == filters["year_id"])
    else:
        # Without a selected year there is no safe class ID to apply. Do not
        # expose legacy classes or duplicate names from unrelated years.
        return []
    return query.all()


def resolve_student_placement(student, academic_year_id=None):
    """Resolve display placement without allowing a newer year to leak in."""
    if not student or not academic_year_id:
        return {}
    return resolve_student_placements([(student, academic_year_id)]).get(
        (student.id, int(academic_year_id)), {}
    )


def resolve_student_placements(student_year_pairs):
    """Resolve many year-specific placements using a bounded set of queries."""
    pairs = {}
    for student, raw_year_id in student_year_pairs:
        year_id = int_or_none(raw_year_id)
        if student and year_id:
            pairs[(student.id, year_id)] = student
    if not pairs:
        return {}

    student_ids = {student_id for student_id, _ in pairs}
    year_ids = {year_id for _, year_id in pairs}
    students = {
        student.id: student
        for student in Student.query.options(
            joinedload(Student.academic_class),
            joinedload(Student.school_class),
            joinedload(Student.academic_section),
        ).filter(Student.id.in_(student_ids)).all()
    }
    enrollments = StudentEnrollment.query.options(
        joinedload(StudentEnrollment.academic_year_level),
        joinedload(StudentEnrollment.academic_year_class),
        joinedload(StudentEnrollment.academic_section),
    ).filter(
        StudentEnrollment.student_id.in_(student_ids),
        StudentEnrollment.academic_year_id.in_(year_ids),
    ).all()
    enrollments_by_scope = {
        (enrollment.student_id, enrollment.academic_year_id): enrollment
        for enrollment in enrollments
    }

    year_levels = AcademicYearLevel.query.filter(
        AcademicYearLevel.academic_year_id.in_(year_ids)
    ).all()
    levels_by_id = {level.id: level for level in year_levels}
    levels_by_legacy = {}
    levels_by_name = {}
    for level in year_levels:
        if level.legacy_level_id:
            levels_by_legacy.setdefault((level.academic_year_id, level.legacy_level_id), []).append(level)
        levels_by_name.setdefault((level.academic_year_id, level.name), []).append(level)

    year_classes = (
        AcademicYearClass.query
        .join(AcademicYearLevel, AcademicYearLevel.id == AcademicYearClass.academic_year_level_id)
        .filter(AcademicYearLevel.academic_year_id.in_(year_ids))
        .all()
    )
    classes_by_id = {year_class.id: year_class for year_class in year_classes}
    classes_by_level_legacy = {}
    for year_class in year_classes:
        if year_class.legacy_class_id:
            classes_by_level_legacy.setdefault(
                (year_class.academic_year_level_id, year_class.legacy_class_id), []
            ).append(year_class)

    placements = {}
    for (student_id, year_id), _ in pairs.items():
        student = students.get(student_id)
        if not student:
            placements[(student_id, year_id)] = {}
            continue

        enrollment = enrollments_by_scope.get((student_id, year_id))
        if enrollment:
            level = levels_by_id.get(enrollment.academic_year_level_id)
            year_class = classes_by_id.get(enrollment.academic_year_class_id)
            section = enrollment.academic_section if enrollment.academic_section_id else None
            valid = bool(
                level
                and level.academic_year_id == year_id
                and year_class
                and year_class.academic_year_level_id == level.id
                and (
                    not enrollment.academic_section_id
                    or (
                        section
                        and year_class.legacy_class_id
                        and section.academic_class_id == year_class.legacy_class_id
                    )
                )
            )
            if not valid:
                placements[(student_id, year_id)] = unresolved_card_placement(
                    year_id, "enrollment", enrollment=enrollment
                )
            else:
                placements[(student_id, year_id)] = {
                    "source": "enrollment",
                    "context_status": "enrollment",
                    "academic_year_id": year_id,
                    "enrollment": enrollment,
                    "academic_year_level_id": enrollment.academic_year_level_id,
                    "academic_year_class_id": enrollment.academic_year_class_id,
                    "academic_level_id": level.legacy_level_id,
                    "academic_class_id": year_class.legacy_class_id,
                    "academic_section_id": enrollment.academic_section_id,
                    "class_name": year_class.name,
                    "level_name": level.name,
                    "section_name": section.name if section else None,
                }
            continue

        if student.academic_year_id != year_id:
            placements[(student_id, year_id)] = {}
            continue

        legacy_level_id = student.academic_level_id or (
            student.academic_class.academic_level_id if student.academic_class else None
        )
        legacy_class_id = student.academic_class_id or (
            student.academic_class.id if student.academic_class else None
        )
        level_candidates = levels_by_legacy.get((year_id, legacy_level_id), []) if legacy_level_id else []
        if not level_candidates and student.level:
            level_candidates = levels_by_name.get((year_id, student.level), [])
        if len(level_candidates) != 1:
            placements[(student_id, year_id)] = unresolved_card_placement(
                year_id, "legacy", legacy_level_id, legacy_class_id, student.academic_section_id
            )
            continue

        level = level_candidates[0]
        class_candidates = (
            classes_by_level_legacy.get((level.id, legacy_class_id), [])
            if legacy_class_id else []
        )
        if legacy_class_id and len(class_candidates) != 1:
            placements[(student_id, year_id)] = unresolved_card_placement(
                year_id, "legacy", legacy_level_id, legacy_class_id, student.academic_section_id
            )
            continue

        year_class = class_candidates[0] if class_candidates else None
        section = student.academic_section if student.academic_section_id else None
        if student.academic_section_id and (
            not year_class or not section or year_class.legacy_class_id != section.academic_class_id
        ):
            placements[(student_id, year_id)] = unresolved_card_placement(
                year_id, "legacy", legacy_level_id, legacy_class_id, student.academic_section_id
            )
            continue

        placements[(student_id, year_id)] = {
            "source": "legacy",
            "context_status": "legacy_compatible",
            "academic_year_id": year_id,
            "enrollment": None,
            "academic_year_level_id": level.id,
            "academic_year_class_id": year_class.id if year_class else None,
            "academic_level_id": legacy_level_id,
            "academic_class_id": legacy_class_id,
            "academic_section_id": student.academic_section_id,
            "legacy_academic_level_id": legacy_level_id,
            "legacy_academic_class_id": legacy_class_id,
            "legacy_academic_section_id": student.academic_section_id,
            "class_name": (
                year_class.name if year_class else
                student.academic_class.name if student.academic_class else
                student.school_class.name if student.school_class else None
            ),
            "level_name": level.name,
            "section_name": section.name if section else student.section,
        }
    return placements


def unresolved_card_placement(
    year_id,
    origin,
    legacy_level_id=None,
    legacy_class_id=None,
    legacy_section_id=None,
    enrollment=None,
):
    context = {
        "source": "unresolved",
        "origin": origin,
        "context_status": "unresolved",
        "academic_year_id": year_id,
        "enrollment": enrollment,
        "academic_year_level_id": None,
        "academic_year_class_id": None,
        "academic_level_id": None,
        "academic_class_id": None,
        "academic_section_id": None,
        "class_name": None,
        "level_name": None,
        "section_name": None,
    }
    if origin == "legacy":
        context.update(
            legacy_academic_level_id=legacy_level_id,
            legacy_academic_class_id=legacy_class_id,
            legacy_academic_section_id=legacy_section_id,
        )
    return context


def int_or_none(value):
    return int(value) if value and str(value).isdigit() else None


def add_months(value, months):
    month = value.month - 1 + months
    year = value.year + month // 12
    month = month % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)
