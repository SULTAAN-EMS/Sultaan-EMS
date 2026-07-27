from datetime import date, datetime

from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func
from sqlalchemy.exc import SQLAlchemyError

from . import db
from .i18n import language_redirect
from .models import AcademicYear, Exam, IdCardIssue, IncidentAction, IncidentCategory, IncidentReport, ReportVerification, Result, SeverityLevel, Student, Subject
from .services import active_exam_for_student, get_settings, result_payload, result_success_overlay_config
from .verification import verification_payload

public_bp = Blueprint("public", __name__)


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
    exam_query = (
        Exam.query.join(Result, Result.exam_id == Exam.id)
        .filter(
            Result.student_id == student.id,
            Result.is_published.is_(True),
        )
    )
    if requested_exam_id:
        exam_query = exam_query.filter(Exam.id == requested_exam_id)
    else:
        exam_query = exam_query.filter(Exam.academic_year_id == student.academic_year_id)
    exam = exam_query.order_by(Exam.id.desc()).first_or_404()

    payload = result_payload(student, exam=exam, public_only=True)
    payload["verification"] = verification_payload(student, exam)
    payload["generated_at"] = datetime.now()
    db.session.commit()

    return render_template("print_report.html", result=payload, settings=settings)


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

    exam = (
        Exam.query.join(Result, Result.exam_id == Exam.id)
        .filter(
            Result.student_id == student.id,
            Result.is_published.is_(True),
            Exam.academic_year_id == student.academic_year_id,
        )
        .order_by(Exam.id.desc())
        .first()
    )

    if not exam:
        return jsonify({"ok": False, "message": "No published result."}), 404

    payload = result_payload(student, exam=exam, public_only=True)

    return jsonify({
        "ok": True,
        "student": {
            "id": student.student_code,
            "name": student.full_name,
            "mother_name": student.mother_name,
            "class": student.school_class.name,
            "academic_year": student.academic_year.name,
        },
        "exam": payload["exam"].name if payload.get("exam") else None,
        "subjects": payload["subjects"],
        "total": payload["total"],
        "average": payload["average"],
        "status": payload["status"],
        "grade": payload["overall_grade"],
    })


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
        flash("Please log in as an invigilator to submit an incident report.", "warning")
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
        category_id = request.form.get("category_id")
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
        if incident_bool_setting(settings_dict, "require_category", True) and not category_id:
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

        if not category_id:
            default_category = IncidentCategory.query.filter_by(is_active=True).order_by(IncidentCategory.sort_order, IncidentCategory.id).first()
            category_id = default_category.id if default_category else None
        if not severity_id:
            default_severity = SeverityLevel.query.filter_by(is_active=True).order_by(SeverityLevel.sort_order, SeverityLevel.id).first()
            severity_id = default_severity.id if default_severity else None
        if not category_id or not severity_id:
            return incident_form_error("Incident categories and severity levels must be configured before submitting reports.")
        if not description:
            description = "No description provided."

        try:
            category = IncidentCategory.query.filter_by(id=int(category_id), is_active=True).first()
            severity = SeverityLevel.query.filter_by(id=int(severity_id), is_active=True).first()
        except (TypeError, ValueError):
            category = severity = None
        if not category:
            return incident_form_error("Please select an active incident category.")
        if not severity:
            return incident_form_error("Please select an active severity level.")

        category_is_other = is_other_lookup_value(category.name)
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
            category_id=category.id,
            severity_id=severity.id,
            exam_id=exam.id if exam else None,
            subject_id=int(request.form.get("subject_id")) if request.form.get("subject_id") else None,
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
    categories = IncidentCategory.query.filter_by(is_active=True).order_by(IncidentCategory.sort_order).all()
    severities = SeverityLevel.query.filter_by(is_active=True).order_by(SeverityLevel.sort_order).all()
    actions = IncidentAction.query.filter_by(is_active=True).order_by(IncidentAction.sort_order).all()
    exams = Exam.query.filter_by(is_published=True).order_by(Exam.id.desc()).all()
    subjects = Subject.query.order_by(Subject.name).all()
    
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
