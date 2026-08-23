import secrets
import calendar
from datetime import date
from tempfile import NamedTemporaryFile

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, send_file, url_for
from flask_login import login_required
from sqlalchemy import or_

from . import db
from .audit import audit
from .models import AcademicClass, AcademicYear, IdCardIssue, SchoolClass, Setting, Student
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
        for key in editable_keys:
            setting = db.session.get(Setting, key) or Setting(key=key)
            setting.value = request.form.get(key, "").strip()
            db.session.add(setting)
        audit("Settings Changes", "Updated ID card designer settings")
        db.session.commit()
        flash("ID card designer saved.", "success")
        return redirect(url_for("admin_id_cards.dashboard"))

    filters = card_filters()
    students = filtered_students(filters).order_by(Student.full_name).limit(500).all()
    issues = IdCardIssue.query.order_by(IdCardIssue.updated_at.desc()).limit(200).all()
    return render_template(
        "admin/id_cards.html",
        settings=get_settings(),
        students=students,
        issues=issues,
        filters=filters,
        classes=SchoolClass.query.order_by(SchoolClass.name).all(),
        years=AcademicYear.query.order_by(AcademicYear.name.desc()).all(),
        levels=distinct_values(Student.level),
        sections=distinct_values(Student.section),
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
    issue = get_or_create_issue(student)
    audit("ID Card Operations", f"Generated ID card for {student.student_code}")
    db.session.commit()
    flash("ID card generated.", "success")
    return redirect(url_for("admin_id_cards.print_cards", issue_ids=issue.id))


@id_cards_bp.route("/bulk-generate", methods=["POST"])
def bulk_generate():
    scope = request.form.get("scope", "selected")
    ids = [int(value) for value in request.form.getlist("student_ids") if value.isdigit()]
    query = Student.query
    if scope == "class" and request.form.get("class_id"):
        query = query.filter_by(class_id=int(request.form["class_id"]))
        ids = [s.id for s in query.all()]
    elif scope == "level" and request.form.get("level"):
        ids = [s.id for s in query.filter_by(level=request.form["level"].strip()).all()]
    elif scope == "section" and request.form.get("section"):
        ids = [s.id for s in query.filter_by(section=request.form["section"].strip()).all()]
    elif scope == "all":
        ids = [s.id for s in query.all()]
    if not ids:
        flash("Select students or choose a valid bulk scope.", "warning")
        return redirect(url_for("admin_id_cards.dashboard"))
    issue_ids = []
    for student in Student.query.filter(Student.id.in_(ids)).all():
        issue_ids.append(get_or_create_issue(student).id)
    audit("ID Card Operations", f"Bulk generated {len(issue_ids)} ID cards")
    db.session.commit()
    flash(f"Generated {len(issue_ids)} ID cards.", "success")
    return redirect(url_for("admin_id_cards.print_cards", issue_ids=",".join(str(i) for i in issue_ids)))


@id_cards_bp.route("/print")
def print_cards():
    issues = selected_issues()
    cards = [{"issue": issue, "qr": id_card_qr_payload(issue)} for issue in issues]
    return render_template("admin/id_card_print.html", cards=cards, settings=get_settings())


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
    pdf.setFillColor(colors.white)
    pdf.roundRect(card_x, card_y, card_w, card_h, 8, stroke=1, fill=1)

    header_h = 22 * mm
    pdf.setFillColor(primary)
    pdf.roundRect(card_x, card_y + card_h - header_h, card_w, header_h, 8, stroke=0, fill=1)
    pdf.setFillColor(colors.white)
    logo_x, logo_y = card_x + 4 * mm, card_y + card_h - header_h + 4 * mm
    pdf.roundRect(logo_x, logo_y, 14 * mm, 14 * mm, 5, stroke=0, fill=1)
    if settings.get("logo_path"):
        logo_path = current_app.static_folder + "/" + settings.get("logo_path").replace("\\", "/")
        try:
            pdf.drawImage(logo_path, logo_x + 1, logo_y + 1, 14 * mm - 2, 14 * mm - 2, preserveAspectRatio=True, mask="auto")
        except Exception:
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
        image_path = current_app.static_folder + "/" + issue.student.photo_path.replace("\\", "/")
        try:
            pdf.drawImage(image_path, photo_x + 1, photo_y + 1, photo_size - 2, photo_size - 2, preserveAspectRatio=True, mask="auto")
        except Exception:
            pass
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


def get_or_create_issue(student, academic_year_id=None):
    """Get/create an ID card in the requested historical year scope."""
    year_id = academic_year_id or student.academic_year_id
    issue = IdCardIssue.query.filter_by(student_id=student.id, academic_year_id=year_id, status="Active").first()
    settings = get_settings()
    months = int(settings.get("id_card_issue_months") or 12)
    if not issue:
        issue = IdCardIssue(
            token=secrets.token_urlsafe(32),
            student=student,
            academic_year_id=year_id,
            issue_date=date.today(),
            expiry_date=add_months(date.today(), months),
            status="Active",
        )
        db.session.add(issue)
        db.session.flush()
    return issue


def selected_issues():
    raw = request.args.get("issue_ids", "")
    ids = [int(value) for value in raw.split(",") if value.isdigit()]
    if ids:
        return IdCardIssue.query.filter(IdCardIssue.id.in_(ids)).order_by(IdCardIssue.id).all()
    return IdCardIssue.query.order_by(IdCardIssue.updated_at.desc()).limit(50).all()


def card_filters():
    return {
        "q": request.args.get("q", "").strip(),
        "class_id": int_or_none(request.args.get("class_id")),
        "year_id": int_or_none(request.args.get("year_id")),
        "level": request.args.get("level", "").strip(),
        "section": request.args.get("section", "").strip(),
    }


def filtered_students(filters):
    query = Student.query
    if filters["year_id"]:
        if filters["class_id"]:
            legacy_school_class = db.session.get(SchoolClass, filters["class_id"])
            academic_class = (
                AcademicClass.query.filter_by(name=legacy_school_class.name).first()
                if legacy_school_class else None
            )
            try:
                query = student_enrollment_legacy_scope_query(
                    filters["year_id"],
                    legacy_class_id=academic_class.id if academic_class else None,
                )
                if not academic_class:
                    query = query.filter(Student.class_id == filters["class_id"])
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
        query = query.filter(Student.level == filters["level"])
    if filters["section"]:
        query = query.filter(Student.section == filters["section"])
    return query


def distinct_values(column):
    return [value[0] for value in db.session.query(column).filter(column.isnot(None), column != "").distinct().order_by(column).all()]


def int_or_none(value):
    return int(value) if value and str(value).isdigit() else None


def add_months(value, months):
    month = value.month - 1 + months
    year = value.year + month // 12
    month = month % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)
