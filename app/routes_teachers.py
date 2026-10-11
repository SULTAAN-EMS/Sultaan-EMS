from datetime import datetime
from tempfile import NamedTemporaryFile
from zoneinfo import ZoneInfo

from flask import Blueprint, abort, flash, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required
from openpyxl import Workbook

from . import db
from .audit import audit
from .cloudinary_service import upload_image
from .models import AcademicClass, AcademicLevel, AcademicSection, AcademicYear, AcademicYearClass, AcademicYearLevel, AcademicYearSubject, Exam, Result, SchoolClass, Setting, Student, Subject, Teacher, TeacherActivity, TeacherResultAssignment, TeacherResultMark, TeacherResultSubmission
from .permissions import enforce_endpoint_permission
from .security import ALLOWED_PHOTOS, allowed_file
from .teacher_services import (
    EMPLOYMENT_TYPES,
    SCHOOL_LEVELS,
    TEACHER_PERMISSIONS,
    create_or_update_teacher_user,
    default_teacher_username,
    generate_teacher_code,
    get_teacher_settings,
    log_teacher_activity,
    parse_teacher_form_dates,
    seed_teacher_settings,
    set_teacher_permissions,
    teacher_activity_summary,
    teacher_list_query,
    teacher_performance_data,
    teacher_permission_set,
)
from .routes_advanced_results import students_for_scope_query, _year_scope_ids_from_legacy
from .services import resolve_subject_max_score

teachers_bp = Blueprint("admin_teachers", __name__)


@teachers_bp.before_request
@login_required
def require_login():
    enforce_endpoint_permission()


@teachers_bp.route("/")
def dashboard():
    return redirect(url_for("admin_teachers.result_entry_setup"))


@teachers_bp.route("/result-entry", methods=["GET", "POST"])
def result_entry_setup():
    """Assign exam/class/subject scopes, set deadlines, and review submissions."""
    if request.method == "POST":
        action = request.form.get("action")
        if action == "create":
            teacher = db.session.get(Teacher, request.form.get("teacher_id", type=int))
            year = db.session.get(AcademicYear, request.form.get("academic_year_id", type=int))
            exam = db.session.get(Exam, request.form.get("exam_id", type=int))
            year_level = db.session.get(AcademicYearLevel, request.form.get("academic_year_level_id", type=int))
            year_class = db.session.get(AcademicYearClass, request.form.get("academic_year_class_id", type=int))
            year_subject = db.session.get(AcademicYearSubject, request.form.get("academic_year_subject_id", type=int))
            school_class = year_class.legacy_class if year_class else None
            subject = year_subject.legacy_subject if year_subject else None
            deadline_raw = (request.form.get("deadline_at") or "").strip()
            try:
                deadline = (
                    datetime.fromisoformat(deadline_raw)
                    .replace(tzinfo=ZoneInfo("Africa/Mogadishu"))
                    .astimezone(ZoneInfo("UTC"))
                    .replace(tzinfo=None)
                    if deadline_raw else None
                )
                max_submissions = max(1, min(10, int(request.form.get("max_submissions", "1"))))
            except (TypeError, ValueError):
                flash("Fadlan hubi deadline-ka iyo tirada gudbinta.", "danger")
                return redirect(url_for("admin_teachers.result_entry_setup"))

            year_scope_matches = bool(
                year and year_level and year_level.is_active
                and year_level.academic_year_id == year.id
                and year_level.legacy_level and year_level.legacy_level.is_active
                and year_class and year_class.is_active
                and year_class.academic_year_level_id == year_level.id
                and school_class and school_class.is_active
                and school_class.academic_level_id == year_level.legacy_level_id
            )
            subject_is_offered = bool(
                year_scope_matches and year_subject and year_subject.is_active
                and year_subject.subject_kind == "exam"
                and year_subject.academic_year_id == year.id
                and year_subject.academic_year_level_id == year_level.id
                and subject and subject.is_active
                and (subject.academic_level_id is None or subject.academic_level_id == year_level.legacy_level_id)
            )
            exam_matches = bool(
                exam and exam.is_active and year_scope_matches
                and exam.academic_year_id == year.id
                and (exam.academic_level_id is None or exam.academic_level_id == year_level.legacy_level_id)
                and (exam.academic_class_id is None or exam.academic_class_id == school_class.id)
            )
            teacher_is_active = bool(teacher and teacher.is_active and teacher.employment_status == "Active")
            if not all((teacher_is_active, year_class, subject_is_offered, exam_matches)):
                flash("Xilsaaristan isma waafaqsana teacher-ka, sanadkan, imtixaanka, fasalka ama maaddada.", "danger")
                return redirect(url_for("admin_teachers.result_entry_setup"))
            duplicate = TeacherResultAssignment.query.filter_by(
                teacher_id=teacher.id, academic_year_id=year.id, exam_id=exam.id,
                class_id=school_class.id, subject_id=subject.id,
            ).first()
            if duplicate:
                duplicate.deadline_at = deadline
                duplicate.max_submissions = max_submissions
                duplicate.is_active = True
                flash("Xilsaaristii hore ayaa la cusboonaysiiyey.", "success")
            else:
                db.session.add(TeacherResultAssignment(
                    teacher=teacher, academic_year=year, exam=exam,
                    school_class=school_class, subject=subject,
                    deadline_at=deadline, max_submissions=max_submissions,
                ))
                flash("Xilsaarista Result Entry waa la sameeyey.", "success")
            audit("Teacher Result Assignment", f"Configured {teacher.full_name} / {exam.name} / {school_class.name} / {subject.name}")
            db.session.commit()
            return redirect(url_for("admin_teachers.result_entry_setup"))

        assignment = db.session.get(TeacherResultAssignment, request.form.get("assignment_id", type=int))
        submission = TeacherResultSubmission.query.filter_by(assignment_id=assignment.id).first() if assignment else None
        if not assignment or not submission:
            abort(404)
        if action == "toggle":
            assignment.is_active = not assignment.is_active
        elif action == "reopen":
            if submission.status not in {"submitted", "approved"}:
                flash("Kaliya submission la gudbiyey ama la ansixiyey ayaa dib loo furi karaa.", "warning")
                return redirect(url_for("admin_teachers.result_entry_setup"))
            reviewer_note = (request.form.get("reviewer_note") or "").strip()
            if not reviewer_note:
                flash("Qor sababta dib loogu furayo xaashida natiijada.", "warning")
                return redirect(url_for("admin_teachers.result_entry_setup"))
            submission.status = "reopened"
            assignment.max_submissions = max(assignment.max_submissions, submission.submission_count + 1)
            submission.reviewer_note = reviewer_note[:500]
        elif action == "approve":
            if submission.status != "submitted":
                flash("Submission-ku waa inuu ku jiraa xaaladda dib-u-eegis.", "warning")
                return redirect(url_for("admin_teachers.result_entry_setup"))
            marks = {mark.student_id: mark for mark in submission.marks}
            year_level, year_class = _year_scope_ids_from_legacy(
                assignment.academic_year_id,
                assignment.school_class.academic_level_id,
                assignment.class_id,
            )
            students = students_for_scope_query(
                assignment.academic_year_id,
                level_id=assignment.school_class.academic_level_id,
                class_id=assignment.class_id,
                exam=assignment.exam,
            ).all()
            if not year_level or not year_class or any(student.id not in marks or marks[student.id].score is None for student in students):
                flash("Natiijadu ma dhammaystirna ama fasalka/sanadku sax ma aha; lama ansixin.", "danger")
                return redirect(url_for("admin_teachers.result_entry_setup"))
            if any(student.is_result_locked for student in students):
                flash("Fasalka waxaa ku jira arday natiijadoodu xiran tahay. Furitaanka natiijooyinkaas ayaa loo baahan yahay ka hor ansixinta.", "danger")
                return redirect(url_for("admin_teachers.result_entry_setup"))
            maximum = resolve_subject_max_score(
                assignment.subject,
                exam=assignment.exam,
                academic_year_level_id=year_level.id,
                academic_level_id=assignment.school_class.academic_level_id,
            )
            if any(marks[student.id].score < 0 or marks[student.id].score > maximum for student in students):
                flash("Dhibco ka baxsan xadka maaddada ayaa jira; submission-ka lama ansixin.", "danger")
                return redirect(url_for("admin_teachers.result_entry_setup"))
            existing_result_count = Result.query.filter(
                Result.exam_id == assignment.exam_id,
                Result.subject_id == assignment.subject_id,
                Result.student_id.in_([student.id for student in students]),
            ).count() if students else 0
            if existing_result_count and request.form.get("confirm_replace") != "yes":
                flash("Dhibco rasmi ah ayaa hore u jira. Isbarbar dhig kadib calaamadee sanduuqa si si cad loogu oggolaado beddeliddooda.", "warning")
                return redirect(url_for("admin_teachers.result_entry_setup"))
            for student in students:
                result = Result.query.filter_by(
                    student_id=student.id, exam_id=assignment.exam_id, subject_id=assignment.subject_id
                ).first()
                if result is None:
                    result = Result(student_id=student.id, exam_id=assignment.exam_id, subject_id=assignment.subject_id, score=marks[student.id].score)
                    db.session.add(result)
                else:
                    result.score = marks[student.id].score
                result.is_published = True
            submission.status = "approved"
            submission.reviewed_at = datetime.utcnow()
            submission.reviewed_by_id = current_user.id
            submission.reviewer_note = (request.form.get("reviewer_note") or "Approved and published.").strip()[:500]
            audit("Teacher Result Approval", f"Approved submission {submission.id} ({len(students)} students; {existing_result_count} existing scores replaced): {assignment.exam.name} / {assignment.school_class.name} / {assignment.subject.name}")
        else:
            abort(400)
        db.session.commit()
        flash("Isbeddelka xilsaarista/submission-ka waa la kaydiyey.", "success")
        return redirect(url_for("admin_teachers.result_entry_setup"))

    all_assignments = TeacherResultAssignment.query.order_by(TeacherResultAssignment.updated_at.desc()).all()
    submission_by_assignment = {
        assignment.id: next(iter(assignment.submissions), None)
        for assignment in all_assignments
    }
    summary = {
        "total": len(all_assignments),
        "review": sum(item is not None and item.status == "submitted" for item in submission_by_assignment.values()),
        "approved": sum(item is not None and item.status == "approved" for item in submission_by_assignment.values()),
        "draft": sum(item is not None and item.status in {"draft", "reopened"} for item in submission_by_assignment.values()),
        "paused": sum(not assignment.is_active for assignment in all_assignments),
    }
    status_filter = request.args.get("status", "all")
    teacher_filter = request.args.get("teacher_id", type=int)
    assignments = all_assignments
    if teacher_filter:
        assignments = [item for item in assignments if item.teacher_id == teacher_filter]
    if status_filter == "review":
        assignments = [item for item in assignments if submission_by_assignment[item.id] and submission_by_assignment[item.id].status == "submitted"]
    elif status_filter == "approved":
        assignments = [item for item in assignments if submission_by_assignment[item.id] and submission_by_assignment[item.id].status == "approved"]
    elif status_filter == "draft":
        assignments = [item for item in assignments if submission_by_assignment[item.id] and submission_by_assignment[item.id].status in {"draft", "reopened"}]
    elif status_filter == "paused":
        assignments = [item for item in assignments if not item.is_active]
    review_rows = {}
    assignments_with_existing_results = set()
    for assignment in assignments:
        submission = next(iter(assignment.submissions), None)
        if not submission or submission.status != "submitted":
            continue
        student_ids = [mark.student_id for mark in submission.marks]
        official_scores = {
            result.student_id: result.score
            for result in Result.query.filter(
                Result.exam_id == assignment.exam_id,
                Result.subject_id == assignment.subject_id,
                Result.student_id.in_(student_ids),
            ).all()
        } if student_ids else {}
        if official_scores:
            assignments_with_existing_results.add(assignment.id)
        review_rows[assignment.id] = [
            {"mark": mark, "official_score": official_scores.get(mark.student_id)}
            for mark in submission.marks
        ]
    deadline_labels = {
        item.id: item.deadline_at.replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo("Africa/Mogadishu"))
        for item in assignments if item.deadline_at
    }
    teachers = Teacher.query.filter_by(is_active=True, employment_status="Active").order_by(Teacher.full_name).all()
    years = AcademicYear.query.order_by(AcademicYear.is_current.desc(), AcademicYear.name.desc()).all()
    year_levels = (
        AcademicYearLevel.query
        .join(AcademicYear, AcademicYear.id == AcademicYearLevel.academic_year_id)
        .filter(AcademicYearLevel.is_active.is_(True), AcademicYearLevel.legacy_level_id.isnot(None))
        .order_by(AcademicYear.is_current.desc(), AcademicYear.name.desc(), AcademicYearLevel.sort_order, AcademicYearLevel.name)
        .all()
    )
    year_levels = [item for item in year_levels if item.legacy_level and item.legacy_level.is_active]
    year_level_ids = {item.id for item in year_levels}
    year_classes = (
        AcademicYearClass.query
        .join(AcademicYearLevel, AcademicYearLevel.id == AcademicYearClass.academic_year_level_id)
        .filter(AcademicYearClass.is_active.is_(True), AcademicYearClass.legacy_class_id.isnot(None))
        .order_by(AcademicYearClass.sort_order, AcademicYearClass.name)
        .all()
    )
    year_classes = [
        item for item in year_classes
        if item.legacy_class and item.legacy_class.is_active
        and item.academic_year_level and item.academic_year_level.is_active
        and item.academic_year_level_id in year_level_ids
        and item.academic_year_level.legacy_level_id == item.legacy_class.academic_level_id
    ]
    year_subjects = (
        AcademicYearSubject.query
        .filter_by(is_active=True, subject_kind="exam")
        .filter(AcademicYearSubject.legacy_subject_id.isnot(None))
        .order_by(AcademicYearSubject.sort_order, AcademicYearSubject.name)
        .all()
    )
    year_subjects = [
        item for item in year_subjects
        if item.legacy_subject and item.legacy_subject.is_active
        and item.academic_year_level and item.academic_year_level.is_active
        and item.academic_year_level_id in year_level_ids
        and item.academic_year_level.academic_year_id == item.academic_year_id
        and item.academic_year_level.legacy_level_id
        and (item.legacy_subject.academic_level_id is None
             or item.legacy_subject.academic_level_id == item.academic_year_level.legacy_level_id)
    ]
    exams = Exam.query.filter_by(is_active=True).order_by(Exam.academic_year_id.desc(), Exam.sort_order, Exam.name).all()
    selected_year_id = request.args.get("setup_year_id", type=int)
    if selected_year_id not in {item.id for item in years}:
        selected_year_id = next((item.id for item in years if item.is_current), years[0].id if years else None)
    available_levels = [item for item in year_levels if item.academic_year_id == selected_year_id]
    selected_year_level_id = request.args.get("setup_level_id", type=int)
    if selected_year_level_id not in {item.id for item in available_levels}:
        selected_year_level_id = available_levels[0].id if available_levels else None
    assignment_scope_details = {}
    for assignment in assignments:
        year_level, year_class = _year_scope_ids_from_legacy(
            assignment.academic_year_id,
            assignment.school_class.academic_level_id,
            assignment.class_id,
        )
        year_subject = AcademicYearSubject.query.filter_by(
            academic_year_id=assignment.academic_year_id,
            academic_year_level_id=year_level.id if year_level else None,
            legacy_subject_id=assignment.subject_id,
            subject_kind="exam",
            is_active=True,
        ).first() if year_level else None
        assignment_scope_details[assignment.id] = {
            "level_name": year_level.name if year_level else (assignment.school_class.academic_level.name if assignment.school_class.academic_level else ""),
            "class_name": year_class.name if year_class else assignment.school_class.name,
            "subject_name": year_subject.name if year_subject else assignment.subject.name,
        }
    return render_template(
        "admin/teachers/result_entry_setup.html",
        assignments=assignments, teachers=teachers, years=years, exams=exams,
        year_levels=year_levels, year_classes=year_classes, year_subjects=year_subjects,
        selected_year_id=selected_year_id, selected_year_level_id=selected_year_level_id,
        assignment_scope_details=assignment_scope_details,
        deadline_labels=deadline_labels,
        review_rows=review_rows,
        assignments_with_existing_results=assignments_with_existing_results,
        summary=summary, status_filter=status_filter, teacher_filter=teacher_filter,
    )


@teachers_bp.route("/list")
def teacher_list():
    filters = {
        "q": request.args.get("q", "").strip(),
        "department": request.args.get("department", "").strip(),
        "school_level": request.args.get("school_level", "").strip(),
        "employment_status": request.args.get("employment_status", "").strip(),
        "subject_id": request.args.get("subject_id", "").strip(),
        "class_id": request.args.get("class_id", "").strip(),
    }
    page = max(request.args.get("page", 1, type=int), 1)
    per_page = 15
    pagination = teacher_list_query(filters).paginate(page=page, per_page=per_page, error_out=False)
    departments = [
        row[0]
        for row in db.session.query(Teacher.department).filter(Teacher.department.isnot(None), Teacher.department != "").distinct().order_by(Teacher.department).all()
    ]
    return render_template(
        "admin/teachers/list.html",
        teachers=pagination.items,
        pagination=pagination,
        filters=filters,
        departments=departments,
        subjects=Subject.query.order_by(Subject.sort_order, Subject.name).all(),
        classes=SchoolClass.query.order_by(SchoolClass.name).all(),
        school_levels=SCHOOL_LEVELS,
    )


@teachers_bp.route("/new", methods=["GET", "POST"])
@teachers_bp.route("/<int:teacher_id>/edit", methods=["GET", "POST"])
def teacher_form(teacher_id=None):
    teacher = db.session.get(Teacher, teacher_id) if teacher_id else Teacher()
    subjects = Subject.query.order_by(Subject.sort_order, Subject.name).all()
    classes = SchoolClass.query.order_by(SchoolClass.name).all()
    academic_levels = AcademicLevel.query.filter_by(is_active=True).order_by(AcademicLevel.sort_order).all()
    settings = get_teacher_settings()

    if request.method == "POST":
        try:
            teacher.teacher_id = request.form["teacher_id"].strip()
            if not teacher.teacher_id:
                raise ValueError("Teacher ID is required and must be entered manually.")
            existing_id = Teacher.query.filter(Teacher.teacher_id == teacher.teacher_id, Teacher.id != teacher.id).first()
            if existing_id:
                raise ValueError("Teacher ID already exists.")

            manual_code = request.form.get("teacher_code", "").strip()
            if manual_code:
                if manual_code != (teacher.teacher_code or ""):
                    teacher.teacher_code = generate_teacher_code(manual_code=manual_code)
            elif not teacher.teacher_code:
                teacher.teacher_code = generate_teacher_code()

            teacher.full_name = request.form["full_name"].strip()
            teacher.gender = request.form.get("gender") or None
            teacher.phone = request.form.get("phone", "").strip()
            teacher.email = request.form.get("email", "").strip()
            teacher.address = request.form.get("address", "").strip()
            teacher.emergency_contact = request.form.get("emergency_contact", "").strip()
            teacher.employment_date = parse_teacher_form_dates(request.form.get("employment_date"))
            teacher.employment_type = request.form.get("employment_type") or "Full Time"
            teacher.qualification = request.form.get("qualification", "").strip()
            teacher.years_experience = int(request.form.get("years_experience") or 0)
            teacher.department = request.form.get("department", "").strip()
            teacher.employment_status = request.form.get("employment_status") or settings.get("teacher_default_status") or "Active"
            teacher.school_level = request.form.get("school_level") or None
            teacher.is_active = teacher.employment_status == "Active"

            photo = request.files.get("photo")
            if photo and photo.filename:
                if not allowed_file(photo.filename, ALLOWED_PHOTOS):
                    raise ValueError("Photo must be JPG, PNG, or WEBP.")
                teacher.photo_path = upload_image(photo, "school/teachers")

            selected_subjects = [int(value) for value in request.form.getlist("subject_ids") if value.isdigit()]
            teacher.subjects = Subject.query.filter(Subject.id.in_(selected_subjects)).all() if selected_subjects else []
            
            # New academic hierarchy assignments
            selected_academic_levels = [int(value) for value in request.form.getlist("academic_level_ids") if value.isdigit()]
            selected_academic_classes = [int(value) for value in request.form.getlist("academic_class_ids") if value.isdigit()]
            selected_academic_sections = [int(value) for value in request.form.getlist("academic_section_ids") if value.isdigit()]
            
            teacher.academic_levels = AcademicLevel.query.filter(AcademicLevel.id.in_(selected_academic_levels)).all() if selected_academic_levels else []
            teacher.classes = AcademicClass.query.filter(AcademicClass.id.in_(selected_academic_classes)).all() if selected_academic_classes else []
            teacher.sections = AcademicSection.query.filter(AcademicSection.id.in_(selected_academic_sections)).all() if selected_academic_sections else []
            
            # Legacy class assignments for backward compatibility
            selected_legacy_classes = [int(value) for value in request.form.getlist("class_ids") if value.isdigit()]
            # Note: We don't assign to teacher.classes here anymore since it now references AcademicClass

            db.session.add(teacher)
            db.session.flush()

            username = request.form.get("username", "").strip() or default_teacher_username(teacher)
            password = request.form.get("password", "")
            confirm = request.form.get("confirm_password", "")
            create_account = bool(request.form.get("create_account"))
            if create_account or teacher.user_id:
                if password and password != confirm:
                    raise ValueError("Passwords do not match.")
                create_or_update_teacher_user(
                    teacher,
                    username=username,
                    password=password,
                    account_active=bool(request.form.get("account_active", "on") == "on"),
                    force_change=bool(request.form.get("force_password_change")),
                )

            log_teacher_activity(teacher, "Profile Updated", f"Saved teacher {teacher.teacher_id} / {teacher.teacher_code}")
            audit("Teacher Updates", f"Saved teacher {teacher.teacher_id} ({teacher.teacher_code})")
            db.session.commit()
            flash("Teacher saved successfully.", "success")
            return redirect(url_for("admin_teachers.teacher_list"))
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "danger")

    return render_template(
        "admin/teachers/form.html",
        teacher=teacher,
        subjects=subjects,
        classes=classes,
        academic_levels=academic_levels,
        settings=settings,
        school_levels=SCHOOL_LEVELS,
        employment_types=EMPLOYMENT_TYPES,
        default_username=default_teacher_username(teacher) if teacher.teacher_id else "",
    )


@teachers_bp.route("/<int:teacher_id>")
def teacher_view(teacher_id):
    teacher = db.session.get(Teacher, teacher_id) or abort(404)
    performance = teacher_performance_data(teacher)[0]
    activity = teacher_activity_summary(teacher)
    return render_template(
        "admin/teachers/view.html",
        teacher=teacher,
        performance=performance,
        activity=activity,
        permissions=teacher_permission_set(teacher),
    )


@teachers_bp.route("/<int:teacher_id>/delete", methods=["POST"])
def delete_teacher(teacher_id):
    teacher = db.session.get(Teacher, teacher_id) or abort(404)
    code = teacher.teacher_code
    teacher_id_value = teacher.teacher_id
    if teacher.user_id:
        user = teacher.user
        teacher.user_id = None
        db.session.flush()
        db.session.delete(user)
    db.session.delete(teacher)
    audit("Teacher Updates", f"Deleted teacher {teacher_id_value} ({code})")
    db.session.commit()
    flash("Teacher deleted.", "success")
    return redirect(url_for("admin_teachers.teacher_list"))


@teachers_bp.route("/<int:teacher_id>/permissions", methods=["GET", "POST"])
def teacher_permissions(teacher_id):
    teacher = db.session.get(Teacher, teacher_id) or abort(404)
    if request.method == "POST":
        posted = request.form.getlist("permissions")
        set_teacher_permissions(teacher, posted)
        log_teacher_activity(teacher, "Permissions Updated", ", ".join(posted))
        audit("Teacher Permissions", f"Updated permissions for {teacher.teacher_id}")
        db.session.commit()
        flash("Teacher permissions saved.", "success")
        return redirect(url_for("admin_teachers.teacher_permissions", teacher_id=teacher.id))
    return render_template(
        "admin/teachers/permissions.html",
        teacher=teacher,
        permissions=TEACHER_PERMISSIONS,
        active_permissions=teacher_permission_set(teacher),
    )


@teachers_bp.route("/performance")
def performance():
    selected_id = request.args.get("teacher_id", type=int)
    teacher = db.session.get(Teacher, selected_id) if selected_id else None
    payloads = teacher_performance_data(teacher)
    return render_template(
        "admin/teachers/performance.html",
        payloads=payloads,
        teachers=Teacher.query.order_by(Teacher.full_name).all(),
        selected_id=selected_id,
    )


@teachers_bp.route("/activity")
def activity():
    teacher_id = request.args.get("teacher_id", type=int)
    teachers = Teacher.query.order_by(Teacher.full_name).all()
    summaries = []
    if teacher_id:
        teacher = db.session.get(Teacher, teacher_id) or abort(404)
        summaries = [teacher_activity_summary(teacher)]
    else:
        summaries = [teacher_activity_summary(teacher) for teacher in teachers[:30]]
    recent = TeacherActivity.query.order_by(TeacherActivity.created_at.desc()).limit(100).all()
    return render_template(
        "admin/teachers/activity.html",
        summaries=summaries,
        teachers=teachers,
        teacher_id=teacher_id,
        recent=recent,
    )


@teachers_bp.route("/settings", methods=["GET", "POST"])
def teacher_settings():
    if request.method == "POST":
        keys = [
            "teacher_code_auto_generate",
            "teacher_code_format",
            "teacher_default_username_format",
            "teacher_password_min_length",
            "teacher_default_password_policy",
            "teacher_theme",
            "teacher_profile_photo_size",
            "teacher_max_upload_size",
            "teacher_default_status",
            "teacher_card_style",
        ]
        for key in keys:
            if key == "teacher_code_auto_generate":
                value = "on" if request.form.get(key) == "on" else "off"
            else:
                value = request.form.get(key, "").strip()
            setting = db.session.get(Setting, key) or Setting(key=key)
            setting.value = value
            db.session.add(setting)
        audit("Teacher Settings", "Updated teacher department settings")
        db.session.commit()
        flash("Teacher settings saved.", "success")
        return redirect(url_for("admin_teachers.teacher_settings"))
    return render_template("admin/teachers/settings.html", settings=get_teacher_settings())


@teachers_bp.route("/export")
def export_teachers():
    wb = Workbook()
    ws = wb.active
    ws.title = "Teachers"
    ws.append(
        [
            "Teacher ID",
            "Teacher Code",
            "Full Name",
            "Phone",
            "Email",
            "Department",
            "School Level",
            "Subjects",
            "Classes",
            "Employment Status",
            "Account Status",
        ]
    )
    for teacher in Teacher.query.order_by(Teacher.full_name).all():
        ws.append(
            [
                teacher.teacher_id,
                teacher.teacher_code,
                teacher.full_name,
                teacher.phone,
                teacher.email,
                teacher.department,
                teacher.school_level,
                ", ".join(subject.name for subject in teacher.subjects),
                ", ".join(school_class.name for school_class in teacher.classes),
                teacher.employment_status,
                "Active" if teacher.is_active else "Inactive",
            ]
        )
    audit("Teacher Export", "Exported teacher list")
    db.session.commit()
    tmp = NamedTemporaryFile(delete=False, suffix=".xlsx")
    wb.save(tmp.name)
    tmp.close()
    return send_file(tmp.name, as_attachment=True, download_name="teachers.xlsx", mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@teachers_bp.route("/print")
def print_teachers():
    filters = {
        "q": request.args.get("q", "").strip(),
        "department": request.args.get("department", "").strip(),
        "school_level": request.args.get("school_level", "").strip(),
        "employment_status": request.args.get("employment_status", "").strip(),
        "subject_id": request.args.get("subject_id", "").strip(),
        "class_id": request.args.get("class_id", "").strip(),
    }
    teachers = teacher_list_query(filters).all()
    return render_template("admin/teachers/print.html", teachers=teachers, generated_at=datetime.now())
