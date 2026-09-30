import secrets
import calendar
from datetime import date
from io import BytesIO
from tempfile import NamedTemporaryFile
from pathlib import Path
from urllib.request import Request, urlopen

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, send_file, url_for
from flask_login import login_required
from sqlalchemy import func, or_

from . import db
from .audit import audit
from .models import AcademicClass, AcademicSection, AcademicYear, AcademicYearClass, AcademicYearLevel, Exam, IdCardIssue, SchoolClass, Setting, Student, StudentEnrollment
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
        .filter_by(academic_year_id=filters["year_id"], is_active=True)
        .order_by(Exam.sort_order, Exam.name, Exam.id)
        .all()
        if filters["year_id"] else []
    )
    students = filtered_students(filters).order_by(Student.full_name).limit(500).all()
    student_placements = {
        student.id: resolve_student_placement(student, filters["year_id"])
        for student in students
    }
    # ID-card generation must only show students with a valid placement in the
    # selected academic year. Unlinked legacy rows stay in the database for
    # other modules, but cannot leak into this year-scoped workflow.
    students = [student for student in students if student_placements.get(student.id)]
    issues = IdCardIssue.query.order_by(IdCardIssue.updated_at.desc()).limit(200).all()
    issue_dates_changed = sync_issue_dates(issues, settings=get_settings())
    if issue_dates_changed:
        db.session.commit()
    issue_placements = {issue.id: resolve_issue_placement(issue) for issue in issues}
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
        classes=filter_class_options(filters),
        years=AcademicYear.query.order_by(AcademicYear.name.desc()).all(),
        levels=filter_level_options(filters),
        sections=filter_section_options(filters),
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
    ids = [int(value) for value in request.form.getlist("student_ids") if value.isdigit()]
    academic_year_id = int_or_none(request.form.get("academic_year_id"))
    exam_id = int_or_none(request.form.get("exam_id"))
    academic_year_class_id = int_or_none(request.form.get("academic_year_class_id"))
    query = Student.query
    has_scope_value = bool(
        (scope == "class" and request.form.get("academic_year_class_id"))
        or (scope == "level" and request.form.get("level", "").strip())
        or (scope == "section" and request.form.get("section", "").strip())
    )
    if scope in {"class", "level", "section"} and academic_year_id and has_scope_value:
        scope_filters = {
            "q": "",
            "year_id": academic_year_id,
            "academic_year_class_id": academic_year_class_id if scope == "class" else None,
            "level": request.form.get("level", "").strip() if scope == "level" else "",
            "section": request.form.get("section", "").strip() if scope == "section" else "",
        }
        ids = [s.id for s in filtered_students(scope_filters).all()]
    elif scope == "class" and request.form.get("class_id"):
        query = query.filter_by(class_id=int(request.form["class_id"]))
        ids = [s.id for s in query.all()]
    elif scope == "level" and request.form.get("level"):
        ids = [s.id for s in query.filter_by(level=request.form["level"].strip()).all()]
    elif scope == "section" and request.form.get("section"):
        ids = [s.id for s in query.filter_by(section=request.form["section"].strip()).all()]
    elif scope == "all" and academic_year_id:
        ids = [s.id for s in student_enrollment_scope_query(academic_year_id).all()]
    elif scope == "all":
        ids = [s.id for s in query.all()]
    if not ids:
        flash("Select students or choose a valid bulk scope.", "warning")
        return redirect(url_for("admin_id_cards.dashboard"))
    issue_ids = []
    try:
        for student in Student.query.filter(Student.id.in_(ids)).all():
            issue_ids.append(get_or_create_issue(student, academic_year_id=academic_year_id, exam_id=exam_id).id)
    except EnrollmentValidationError as exc:
        db.session.rollback()
        flash(str(exc), "warning")
        return redirect(url_for("admin_id_cards.dashboard"))
    audit("ID Card Operations", f"Bulk generated {len(issue_ids)} ID cards")
    db.session.commit()
    flash(f"Generated {len(issue_ids)} ID cards.", "success")
    return redirect(url_for("admin_id_cards.print_cards", issue_ids=",".join(str(i) for i in issue_ids)))


@id_cards_bp.route("/print")
def print_cards():
    issues = selected_issues()
    settings = get_settings()
    if sync_issue_dates(issues, settings=settings):
        db.session.commit()
    cards = [
        {
            "issue": issue,
            "placement": resolve_issue_placement(issue),
            "qr": id_card_qr_payload(issue),
        }
        for issue in issues
    ]
    return render_template("admin/id_card_print.html", cards=cards, settings=settings)


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
    issue = IdCardIssue.query.filter_by(
        student_id=student.id,
        academic_year_id=year_id,
        exam_id=exam.id,
        status="Active",
    ).first()
    settings = get_settings()
    months = configured_issue_months(settings)
    if issue:
        ensure_issue_dates(issue, months=months)
        if effective_issue_status(issue) != "Active":
            issue = None
    if not issue:
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
    from .enrollment_service import resolve_student_academic_context

    placement = resolve_student_academic_context(issue.student, issue.academic_year_id)
    if placement:
        return placement
    if issue.student.academic_year_id != issue.academic_year_id:
        return {}
    return {
        "source": "legacy",
        "academic_year_id": issue.academic_year_id,
        "class_name": issue.student.academic_class.name if issue.student.academic_class else issue.student.school_class.name if issue.student.school_class else None,
        "level_name": issue.student.academic_level.name if issue.student.academic_level else issue.student.level,
        "section_name": issue.student.academic_section.name if issue.student.academic_section else issue.student.section,
    }


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
    if ids:
        return IdCardIssue.query.filter(IdCardIssue.id.in_(ids)).order_by(IdCardIssue.id).all()
    return IdCardIssue.query.order_by(IdCardIssue.updated_at.desc()).limit(50).all()


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
        "level": request.args.get("level", "").strip(),
        "section": request.args.get("section", "").strip(),
    }


def filtered_students(filters):
    query = Student.query
    if filters["year_id"]:
        if filters["academic_year_class_id"]:
            try:
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
            level_ids = [
                row.id
                for row in AcademicYearLevel.query.filter_by(
                    academic_year_id=filters["year_id"],
                    name=filters["level"],
                ).all()
            ]
            query = query.filter(
                or_(
                    StudentEnrollment.academic_year_level_id.in_(level_ids),
                    Student.level == filters["level"],
                )
            )
    if filters["section"]:
        section_ids = [
            row.id for row in AcademicSection.query.filter_by(name=filters["section"]).all()
        ]
        if filters["year_id"]:
            query = query.filter(
                or_(
                    StudentEnrollment.academic_section_id.in_(section_ids),
                    Student.section == filters["section"],
                )
            )
        else:
            query = query.filter(Student.section == filters["section"])
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
    if academic_year_id:
        from .enrollment_service import resolve_student_academic_context

        placement = resolve_student_academic_context(student, academic_year_id)
        if placement:
            return placement
        if student.academic_year_id != academic_year_id:
            return {}
        return {
            "source": "legacy",
            "academic_year_id": academic_year_id,
            "class_name": student.academic_class.name if student.academic_class else student.school_class.name if student.school_class else None,
            "level_name": student.academic_level.name if student.academic_level else student.level,
            "section_name": student.academic_section.name if student.academic_section else student.section,
        }
    return {}


def int_or_none(value):
    return int(value) if value and str(value).isdigit() else None


def add_months(value, months):
    month = value.month - 1 + months
    year = value.year + month // 12
    month = month % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)
