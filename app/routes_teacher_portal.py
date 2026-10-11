import io
from datetime import datetime
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from flask import Blueprint, abort, flash, jsonify, make_response, redirect, render_template, request, url_for
from flask_login import current_user, login_user, logout_user
from openpyxl import Workbook
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from . import db
from .models import (
    AcademicClass, AcademicYear, AcademicYearClass, AcademicYearLevel, Exam, Result,
    Student, Subject, TeacherResultAssignment,
    TeacherResultMark, TeacherResultSubmission,
)
from .teacher_analytics import (
    classes_cards,
    comparison_data,
    dashboard_summary,
    examinations_list_data,
    exam_analysis_data,
    filter_options,
    generate_ai_insights,
    grade_distribution_data,
    pass_fail_analysis_data,
    performance_trends_data,
    report_summary,
    results_list_data,
    student_analysis_data,
    students_list_data,
    subject_analysis_data,
    subjects_cards,
    top_performers_data,
    validate_filter_ids,
    weak_students_data,
)
from .teacher_portal import (
    TEACHER_PUBLIC_ENDPOINTS,
    get_current_teacher,
    is_teacher_account,
    parse_teacher_filters,
    record_teacher_visit,
    safe_teacher_next_url,
    teacher_can,
    teacher_login_required,
    teacher_nav_items,
    teacher_permission_required,
    teacher_requires_password_change,
    teacher_theme,
)
from .teacher_services import (
    authenticate_teacher_login,
    get_teacher_settings,
    log_teacher_activity,
    record_teacher_login,
    record_teacher_logout,
    request_teacher_password_reset,
    teacher_activity_summary,
    validate_teacher_password,
)
from .services import resolve_subject_max_score
from .routes_advanced_results import students_for_scope_query, _year_scope_ids_from_legacy

teacher_portal_bp = Blueprint("teacher_portal", __name__)


@teacher_portal_bp.before_request
def protect_teacher_portal():
    if not request.endpoint or not request.endpoint.startswith("teacher_portal."):
        return None
    if request.endpoint in TEACHER_PUBLIC_ENDPOINTS:
        return None
    return None


def _ctx(teacher, filters, page_title, active_endpoint):
    options = filter_options(teacher, filters)
    return {
        "teacher": teacher,
        "filters": filters,
        "filter_options": options,
        "page_title": page_title,
        "active_endpoint": active_endpoint,
        "nav_items": teacher_nav_items(),
        "teacher_can": teacher_can,
        "teacher_theme": teacher_theme(),
        "teacher_settings": get_teacher_settings(),
    }


@teacher_portal_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated and is_teacher_account():
        return redirect(safe_teacher_next_url(request.args.get("next")))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user, teacher, error = authenticate_teacher_login(username, password)
        if error:
            flash(error, "danger")
        else:
            login_user(user)
            record_teacher_login(teacher)
            db.session.commit()
            return redirect(safe_teacher_next_url(request.args.get("next")))
    return render_template("teacher/login.html")


@teacher_portal_bp.route("/logout", methods=["POST"])
@teacher_login_required
def logout():
    teacher = get_current_teacher()
    record_teacher_logout(teacher)
    db.session.commit()
    logout_user()
    flash("You have been logged out.", "success")
    return redirect(url_for("teacher_portal.login"))


@teacher_portal_bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if current_user.is_authenticated and is_teacher_account():
        return redirect(url_for("teacher_portal.dashboard"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        request_teacher_password_reset(username)
        db.session.commit()
        flash("If the account exists, your request has been logged. Please contact your school administrator.", "success")
        return redirect(url_for("teacher_portal.login"))
    return render_template("teacher/forgot_password.html")


@teacher_portal_bp.route("/change-password", methods=["GET", "POST"])
@teacher_login_required
def change_password():
    teacher = get_current_teacher()
    if request.method == "POST":
        current = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "")
        confirm = request.form.get("confirm_password", "")
        if not current_user.check_password(current):
            flash("Current password is incorrect.", "danger")
        else:
            error = validate_teacher_password(new_password)
            if error:
                flash(error, "danger")
            elif new_password != confirm:
                flash("Passwords do not match.", "danger")
            else:
                current_user.set_password(new_password)
                teacher.force_password_change = False
                log_teacher_activity(teacher, "Password Change", "Updated account password")
                db.session.commit()
                flash("Password changed successfully.", "success")
                return redirect(safe_teacher_next_url(None))
    return render_template(
        "teacher/change_password.html",
        forced=teacher_requires_password_change(),
        teacher_theme=teacher_theme(),
    )


@teacher_portal_bp.route("/")
@teacher_login_required
def dashboard():
    teacher = get_current_teacher()
    filters = validate_filter_ids(teacher, parse_teacher_filters(request.args))
    record_teacher_visit(teacher, "Dashboard")
    ctx = _ctx(teacher, filters, "Teacher Analysis Dashboard", "teacher_portal.dashboard")
    ctx["summary"] = dashboard_summary(teacher, filters)
    db.session.commit()
    return render_template("teacher/dashboard.html", **ctx)


@teacher_portal_bp.route("/result-entry")
@teacher_login_required
def result_entry():
    teacher = get_current_teacher()
    assignments = (
        TeacherResultAssignment.query
        .filter_by(teacher_id=teacher.id, is_active=True)
        .order_by(TeacherResultAssignment.deadline_at.is_(None), TeacherResultAssignment.deadline_at, TeacherResultAssignment.id)
        .all()
    )
    selected_id = request.args.get("assignment_id", type=int)
    assignment = next((item for item in assignments if item.id == selected_id), None)
    if assignment is None and assignments:
        assignment = assignments[0]
    deadline_labels = {
        item.id: item.deadline_at.replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo("Africa/Mogadishu"))
        for item in assignments if item.deadline_at
    }

    rows = []
    submission = None
    max_score = None
    locked = False
    if assignment:
        year_level, year_class = _year_scope_ids_from_legacy(
            assignment.academic_year_id,
            assignment.school_class.academic_level_id,
            assignment.class_id,
        )
        students = []
        if year_level and year_class and assignment.exam.academic_year_id == assignment.academic_year_id:
            students = students_for_scope_query(
                assignment.academic_year_id,
                level_id=assignment.school_class.academic_level_id,
                class_id=assignment.class_id,
                exam=assignment.exam,
            ).order_by(Student.full_name).all()
        submission = TeacherResultSubmission.query.filter_by(assignment_id=assignment.id).first()
        saved_marks = {mark.student_id: mark for mark in submission.marks} if submission else {}
        official_marks = {
            result.student_id: result
            for result in Result.query.filter_by(exam_id=assignment.exam_id, subject_id=assignment.subject_id)
            .filter(Result.student_id.in_([student.id for student in students])).all()
        } if students else {}
        for student in students:
            draft = saved_marks.get(student.id)
            existing = official_marks.get(student.id)
            rows.append({
                "student": student,
                "score": draft.score if draft and draft.score is not None else (existing.score if existing else None),
                "is_existing": bool(existing),
            })
        max_score = resolve_subject_max_score(
            assignment.subject,
            exam=assignment.exam,
            academic_year_level_id=year_level.id if year_level else None,
            academic_level_id=assignment.school_class.academic_level_id,
        )
        deadline_passed = bool(assignment.deadline_at and datetime.utcnow() > assignment.deadline_at)
        reopened = bool(submission and submission.status == "reopened")
        locked = (deadline_passed and not reopened) or bool(submission and submission.status in {"submitted", "approved"})

    return render_template(
        "teacher/result_entry.html",
        teacher=teacher,
        page_title="Result Entry",
        active_endpoint="teacher_portal.result_entry",
        nav_items=teacher_nav_items(),
        teacher_can=teacher_can,
        teacher_theme=teacher_theme(),
        teacher_settings=get_teacher_settings(),
        assignments=assignments,
        deadline_labels=deadline_labels,
        assignment=assignment,
        submission=submission,
        rows=rows,
        max_score=max_score,
        locked=locked,
        now=datetime.utcnow(),
    )


@teacher_portal_bp.route("/result-entry/save", methods=["POST"])
@teacher_login_required
def result_entry_save():
    teacher = get_current_teacher()
    assignment = TeacherResultAssignment.query.filter_by(
        id=request.form.get("assignment_id", type=int), teacher_id=teacher.id, is_active=True
    ).first_or_404()
    now = datetime.utcnow()
    submission = TeacherResultSubmission.query.filter_by(assignment_id=assignment.id).first()
    if submission and submission.status in {"submitted", "approved"}:
        flash("Natiijadan waa la gudbiyey oo waa xiran tahay. Maamulka ayaa dib u furi kara.", "warning")
        return redirect(url_for("teacher_portal.result_entry", assignment_id=assignment.id))
    if assignment.deadline_at and now > assignment.deadline_at and not (submission and submission.status == "reopened"):
        flash("Waqtigii gudbinta natiijadan wuu dhammaaday.", "danger")
        return redirect(url_for("teacher_portal.result_entry", assignment_id=assignment.id))
    if submission and submission.submission_count >= assignment.max_submissions:
        flash("Tirada gudbinta ee laguu oggolaaday way dhammaatay. La xiriir maamulka.", "danger")
        return redirect(url_for("teacher_portal.result_entry", assignment_id=assignment.id))

    year_level, year_class = _year_scope_ids_from_legacy(
        assignment.academic_year_id,
        assignment.school_class.academic_level_id,
        assignment.class_id,
    )
    if not year_level or not year_class or assignment.exam.academic_year_id != assignment.academic_year_id:
        abort(409, "The academic assignment is no longer valid.")
    students = students_for_scope_query(
        assignment.academic_year_id,
        level_id=assignment.school_class.academic_level_id,
        class_id=assignment.class_id,
        exam=assignment.exam,
    ).order_by(Student.full_name).all()
    score_by_student = {}
    max_score = Decimal(str(resolve_subject_max_score(
        assignment.subject,
        exam=assignment.exam,
        academic_year_level_id=year_level.id,
        academic_level_id=assignment.school_class.academic_level_id,
    )))
    errors = []
    submit_now = request.form.get("action") == "submit"
    for student in students:
        raw = (request.form.get(f"score_{student.id}") or "").strip()
        if not raw:
            if submit_now:
                errors.append(student.full_name)
            score_by_student[student.id] = None
            continue
        try:
            score = Decimal(raw)
        except InvalidOperation:
            errors.append(student.full_name)
            continue
        if not score.is_finite() or score < 0 or score > max_score:
            errors.append(student.full_name)
            continue
        score_by_student[student.id] = score
    if errors:
        flash("Dhibcaha qaar waa maqan yihiin ama kama dhex jiraan 0 iyo %s: %s" % (max_score, ", ".join(errors[:6])), "danger")
        return redirect(url_for("teacher_portal.result_entry", assignment_id=assignment.id))

    if not submission:
        submission = TeacherResultSubmission(assignment=assignment)
        db.session.add(submission)
        db.session.flush()
    mark_by_student = {mark.student_id: mark for mark in submission.marks}
    for student in students:
        mark = mark_by_student.get(student.id)
        if not mark:
            mark = TeacherResultMark(submission=submission, student_id=student.id)
            db.session.add(mark)
        mark.score = score_by_student.get(student.id)
    if submit_now:
        if submission.submission_count >= assignment.max_submissions:
            db.session.rollback()
            flash("Tirada gudbinta ee laguu oggolaaday way dhammaatay.", "danger")
            return redirect(url_for("teacher_portal.result_entry", assignment_id=assignment.id))
        submission.status = "submitted"
        submission.submission_count += 1
        submission.submitted_at = now
        flash("Natiijada si guul leh ayaa loogu gudbiyey maamulka si dib loogu eego.", "success")
    else:
        if submission.status != "reopened":
            submission.status = "draft"
        flash("Qabyada natiijada waa la kaydiyey.", "success")
    db.session.commit()
    return redirect(url_for("teacher_portal.result_entry", assignment_id=assignment.id))




@teacher_portal_bp.route("/subjects")
@teacher_login_required
@teacher_permission_required("view_assigned_subjects")
def subjects():
    teacher = get_current_teacher()
    filters = validate_filter_ids(teacher, parse_teacher_filters(request.args))
    record_teacher_visit(teacher, "My Subjects")
    ctx = _ctx(teacher, filters, "My Subjects", "teacher_portal.subjects")
    ctx["cards"] = subjects_cards(teacher, filters)
    db.session.commit()
    return render_template("teacher/subjects.html", **ctx)


@teacher_portal_bp.route("/classes")
@teacher_login_required
@teacher_permission_required("view_assigned_classes")
def classes():
    teacher = get_current_teacher()
    filters = validate_filter_ids(teacher, parse_teacher_filters(request.args))
    record_teacher_visit(teacher, "My Classes")
    ctx = _ctx(teacher, filters, "My Classes", "teacher_portal.classes")
    ctx["cards"] = classes_cards(teacher, filters)
    db.session.commit()
    return render_template("teacher/classes.html", **ctx)


@teacher_portal_bp.route("/profile")
@teacher_login_required
def profile():
    teacher = get_current_teacher()
    filters = validate_filter_ids(teacher, parse_teacher_filters(request.args))
    record_teacher_visit(teacher, "My Profile")
    ctx = _ctx(teacher, filters, "My Profile", "teacher_portal.profile")
    ctx["activity"] = teacher_activity_summary(teacher)
    db.session.commit()
    return render_template("teacher/profile.html", **ctx)


@teacher_portal_bp.route("/my-students")
@teacher_login_required
@teacher_permission_required("view_students")
def my_students():
    teacher = get_current_teacher()
    filters = validate_filter_ids(teacher, parse_teacher_filters(request.args))
    record_teacher_visit(teacher, "My Students")
    ctx = _ctx(teacher, filters, "My Students", "teacher_portal.my_students")
    ctx["rows"] = students_list_data(teacher, filters)
    db.session.commit()
    return render_template("teacher/my_students.html", **ctx)


@teacher_portal_bp.route("/examinations")
@teacher_login_required
@teacher_permission_required("view_examinations")
def examinations():
    teacher = get_current_teacher()
    filters = validate_filter_ids(teacher, parse_teacher_filters(request.args))
    record_teacher_visit(teacher, "Examinations")
    ctx = _ctx(teacher, filters, "Examinations", "teacher_portal.examinations")
    ctx["rows"] = examinations_list_data(teacher, filters)
    db.session.commit()
    return render_template("teacher/examinations.html", **ctx)


@teacher_portal_bp.route("/results")
@teacher_login_required
@teacher_permission_required("view_student_results")
def results():
    teacher = get_current_teacher()
    filters = validate_filter_ids(teacher, parse_teacher_filters(request.args))
    record_teacher_visit(teacher, "Results")
    ctx = _ctx(teacher, filters, "Results", "teacher_portal.results")
    ctx["rows"] = results_list_data(teacher, filters)
    db.session.commit()
    return render_template("teacher/results.html", **ctx)


@teacher_portal_bp.route("/exam-analysis")
@teacher_login_required
@teacher_permission_required("future_analysis_features")
def exam_analysis():
    teacher = get_current_teacher()
    filters = validate_filter_ids(teacher, parse_teacher_filters(request.args))
    record_teacher_visit(teacher, "Exam Analysis")
    ctx = _ctx(teacher, filters, "Exam Analysis", "teacher_portal.exam_analysis")
    ctx["analysis"] = exam_analysis_data(teacher, filters)
    db.session.commit()
    return render_template("teacher/exam_analysis.html", **ctx)


@teacher_portal_bp.route("/subject-analysis")
@teacher_login_required
@teacher_permission_required("future_analysis_features")
def subject_analysis():
    teacher = get_current_teacher()
    filters = validate_filter_ids(teacher, parse_teacher_filters(request.args))
    record_teacher_visit(teacher, "Subject Analysis")
    ctx = _ctx(teacher, filters, "Subject Analysis", "teacher_portal.subject_analysis")
    ctx["analysis"] = subject_analysis_data(teacher, filters)
    db.session.commit()
    return render_template("teacher/subject_analysis.html", **ctx)


@teacher_portal_bp.route("/students")
@teacher_login_required
@teacher_permission_required("view_student_profiles")
def student_analysis():
    teacher = get_current_teacher()
    filters = validate_filter_ids(teacher, parse_teacher_filters(request.args))
    record_teacher_visit(teacher, "Student Analysis")
    ctx = _ctx(teacher, filters, "Student Analysis", "teacher_portal.student_analysis")
    if filters.get("student_id"):
        ctx["analysis"] = student_analysis_data(teacher, filters["student_id"], filters)
        if ctx["analysis"] is None:
            abort(403)
    else:
        ctx["analysis"] = None
    db.session.commit()
    return render_template("teacher/student_analysis.html", **ctx)


@teacher_portal_bp.route("/trends")
@teacher_login_required
@teacher_permission_required("future_analysis_features")
def trends():
    teacher = get_current_teacher()
    filters = validate_filter_ids(teacher, parse_teacher_filters(request.args))
    record_teacher_visit(teacher, "Performance Trends")
    ctx = _ctx(teacher, filters, "Performance Trends", "teacher_portal.trends")
    ctx["trends"] = performance_trends_data(teacher, filters)
    db.session.commit()
    return render_template("teacher/trends.html", **ctx)


@teacher_portal_bp.route("/grades")
@teacher_login_required
@teacher_permission_required("future_analysis_features")
def grades():
    teacher = get_current_teacher()
    filters = validate_filter_ids(teacher, parse_teacher_filters(request.args))
    record_teacher_visit(teacher, "Grade Distribution")
    ctx = _ctx(teacher, filters, "Grade Distribution", "teacher_portal.grades")
    ctx["distribution"] = grade_distribution_data(teacher, filters)
    db.session.commit()
    return render_template("teacher/grades.html", **ctx)


@teacher_portal_bp.route("/pass-fail")
@teacher_login_required
@teacher_permission_required("future_analysis_features")
def pass_fail():
    teacher = get_current_teacher()
    filters = validate_filter_ids(teacher, parse_teacher_filters(request.args))
    record_teacher_visit(teacher, "Pass / Fail Analysis")
    ctx = _ctx(teacher, filters, "Pass / Fail Analysis", "teacher_portal.pass_fail")
    ctx["analysis"] = pass_fail_analysis_data(teacher, filters)
    db.session.commit()
    return render_template("teacher/pass_fail.html", **ctx)


@teacher_portal_bp.route("/top-performers")
@teacher_login_required
@teacher_permission_required("future_analysis_features")
def top_performers():
    teacher = get_current_teacher()
    filters = validate_filter_ids(teacher, parse_teacher_filters(request.args))
    record_teacher_visit(teacher, "Top Performers")
    ctx = _ctx(teacher, filters, "Top Performers", "teacher_portal.top_performers")
    ctx["data"] = top_performers_data(teacher, filters)
    db.session.commit()
    return render_template("teacher/top_performers.html", **ctx)


@teacher_portal_bp.route("/weak-students")
@teacher_login_required
@teacher_permission_required("future_analysis_features")
def weak_students():
    teacher = get_current_teacher()
    filters = validate_filter_ids(teacher, parse_teacher_filters(request.args))
    record_teacher_visit(teacher, "Weak Students")
    ctx = _ctx(teacher, filters, "Weak Students", "teacher_portal.weak_students")
    ctx["data"] = weak_students_data(teacher, filters)
    db.session.commit()
    return render_template("teacher/weak_students.html", **ctx)


@teacher_portal_bp.route("/comparison")
@teacher_login_required
@teacher_permission_required("future_analysis_features")
def comparison():
    teacher = get_current_teacher()
    filters = validate_filter_ids(teacher, parse_teacher_filters(request.args))
    record_teacher_visit(teacher, "Comparison")
    ctx = _ctx(teacher, filters, "Comparison", "teacher_portal.comparison")
    ctx["comparison"] = comparison_data(teacher, filters)
    db.session.commit()
    return render_template("teacher/comparison.html", **ctx)


@teacher_portal_bp.route("/reports")
@teacher_login_required
@teacher_permission_required("generate_reports")
def reports():
    teacher = get_current_teacher()
    filters = validate_filter_ids(teacher, parse_teacher_filters(request.args))
    record_teacher_visit(teacher, "Reports")
    ctx = _ctx(teacher, filters, "Reports", "teacher_portal.reports")
    ctx["report"] = report_summary(teacher, filters)
    db.session.commit()
    return render_template("teacher/reports.html", **ctx)


@teacher_portal_bp.route("/ai-insights")
@teacher_login_required
@teacher_permission_required("future_ai_features")
def ai_insights():
    teacher = get_current_teacher()
    filters = validate_filter_ids(teacher, parse_teacher_filters(request.args))
    record_teacher_visit(teacher, "AI Insights")
    ctx = _ctx(teacher, filters, "AI Insights", "teacher_portal.ai_insights")
    ctx["insights"] = generate_ai_insights(teacher, filters)
    db.session.commit()
    return render_template("teacher/ai_insights.html", **ctx)


@teacher_portal_bp.route("/settings", methods=["GET", "POST"])
@teacher_login_required
def settings():
    from .models import Setting

    teacher = get_current_teacher()
    filters = validate_filter_ids(teacher, parse_teacher_filters(request.args))
    settings_data = get_teacher_settings()
    if request.method == "POST":
        theme = request.form.get("teacher_theme", "light")
        if theme in ("light", "dark"):
            row = db.session.get(Setting, "teacher_theme")
            if not row:
                row = Setting(key="teacher_theme", value=theme)
                db.session.add(row)
            else:
                row.value = theme
            log_teacher_activity(teacher, "Settings", f"Updated theme to {theme}")
            db.session.commit()
            flash("Settings saved.", "success")
            return redirect(url_for("teacher_portal.settings"))
    ctx = _ctx(teacher, filters, "Settings", "teacher_portal.settings")
    ctx["settings_data"] = settings_data
    return render_template("teacher/settings.html", **ctx)


@teacher_portal_bp.route("/api/filter-options")
@teacher_login_required
def api_filter_options():
    teacher = get_current_teacher()
    filters = validate_filter_ids(teacher, parse_teacher_filters(request.args))
    options = filter_options(teacher, filters)
    return jsonify(
        {
            "academic_years": [{"id": year.id, "name": year.name} for year in options["academic_years"]],
            "exams": [{"id": exam.id, "name": exam.name} for exam in options["exams"]],
            "subjects": [{"id": subject.id, "name": subject.name} for subject in options["subjects"]],
            "classes": [{"id": school_class.id, "name": school_class.name} for school_class in options["classes"]],
            "sections": options["sections"],
            "students": [{"id": student.id, "name": student.full_name, "code": student.student_code} for student in options["students"]],
        }
    )


@teacher_portal_bp.route("/reports/export/excel")
@teacher_login_required
@teacher_permission_required("export_excel")
def export_excel():
    teacher = get_current_teacher()
    filters = validate_filter_ids(teacher, parse_teacher_filters(request.args))
    report = report_summary(teacher, filters)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Teacher Summary"
    sheet.append(["Teacher Analysis Report", teacher.full_name, datetime.utcnow().strftime("%Y-%m-%d %H:%M")])
    sheet.append([])
    sheet.append(["Metric", "Value"])
    for key, value in report["dashboard"].items():
        if key not in ("trends", "chart_labels", "chart_values"):
            sheet.append([key.replace("_", " ").title(), value])
    sheet.append([])
    sheet.append(["Subject", "Average", "Pass Rate"])
    for card in report["subjects"]:
        sheet.append([card["subject"].name, card["average"], card["pass_rate"]])
    log_teacher_activity(teacher, "Download", "Exported Excel report")
    db.session.commit()
    output = io.BytesIO()
    workbook.save(output)
    output.seek(0)
    response = make_response(output.read())
    response.headers["Content-Type"] = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    response.headers["Content-Disposition"] = f'attachment; filename=teacher-report-{teacher.teacher_code}.xlsx'
    return response


@teacher_portal_bp.route("/reports/export/pdf")
@teacher_login_required
@teacher_permission_required("export_pdf")
def export_pdf():
    teacher = get_current_teacher()
    filters = validate_filter_ids(teacher, parse_teacher_filters(request.args))
    report = report_summary(teacher, filters)
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4), rightMargin=36, leftMargin=36, topMargin=36, bottomMargin=24)
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("Title", parent=styles["Heading1"], textColor=colors.HexColor("#002060"))
    story = [
        Paragraph("Teacher Analysis Report", title_style),
        Paragraph(f"{teacher.full_name} ({teacher.teacher_code})", styles["Normal"]),
        Spacer(1, 0.2 * inch),
    ]
    rows = [["Metric", "Value"]]
    for key, value in report["dashboard"].items():
        if key not in ("trends", "chart_labels", "chart_values"):
            rows.append([key.replace("_", " ").title(), str(value)])
    table = Table(rows, colWidths=[3 * inch, 2 * inch])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#002060")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ]
        )
    )
    story.append(table)
    story.append(Spacer(1, 0.2 * inch))
    story.append(Paragraph("AI Insights", styles["Heading2"]))
    for insight in report["insights"]:
        story.append(Paragraph(f"• {insight}", styles["Normal"]))
    doc.build(story)
    log_teacher_activity(teacher, "Report Generated", "Exported PDF report")
    db.session.commit()
    buffer.seek(0)
    response = make_response(buffer.read())
    response.headers["Content-Type"] = "application/pdf"
    response.headers["Content-Disposition"] = f'attachment; filename=teacher-report-{teacher.teacher_code}.pdf'
    return response


@teacher_portal_bp.route("/reports/print")
@teacher_login_required
@teacher_permission_required("print_reports")
def print_report():
    teacher = get_current_teacher()
    filters = validate_filter_ids(teacher, parse_teacher_filters(request.args))
    ctx = _ctx(teacher, filters, "Printable Report", "teacher_portal.reports")
    ctx["report"] = report_summary(teacher, filters)
    ctx["generated_at"] = datetime.utcnow()
    return render_template("teacher/print_report.html", **ctx)
