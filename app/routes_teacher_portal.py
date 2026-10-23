import io
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from flask import Blueprint, abort, flash, jsonify, make_response, redirect, render_template, request, session, url_for
from flask_login import current_user, login_user, logout_user
from openpyxl import Workbook
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from sqlalchemy.orm import joinedload

from . import db
from .models import (
    AcademicClass, AcademicLevel, AcademicYear, AcademicYearClass, AcademicYearLevel, ClubTeamLeaderAssignment,
    ClubTeamMembership, Exam, ExamMarkingConfiguration, Result, Student, Subject, TeacherResultAssignment,
    TeacherResultMark, TeacherResultSubmission,
)
from .teacher_result_history import record_teacher_result_event, record_teacher_result_mark_revision
from .teacher_analytics import (
    classes_cards,
    comparison_data,
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
from .services import load_grade_scale_cache, resolve_subject_max_score
from .routes_advanced_results import students_for_scope_query, _year_scope_ids_from_legacy

teacher_portal_bp = Blueprint("teacher_portal", __name__)

RESULT_ENTRY_FILTER_KEYS = (
    "academic_year_id", "exam_id", "academic_level_id", "class_id", "subject_id", "status",
)


def _parse_result_entry_score(raw_value):
    try:
        score = Decimal(str(raw_value).strip().replace(",", "."))
    except (InvalidOperation, ValueError):
        return None, "invalid"
    if not score.is_finite() or score < 0:
        return None, "invalid"
    try:
        normalized = score.quantize(Decimal("0.01"))
    except InvalidOperation:
        return None, "invalid"
    if score != normalized:
        return None, "precision"
    return normalized, None


def _set_result_mark_action(mark, previous_score, next_score, occurred_at):
    if previous_score is None and next_score is not None:
        action = "entered"
    elif previous_score is not None and next_score is None:
        action = "cleared"
    elif previous_score != next_score:
        action = "edited"
    else:
        return None
    mark.last_action = action
    mark.last_action_at = occurred_at
    mark.last_action_by_id = current_user.id if current_user.is_authenticated else None
    return action


def _next_result_submission_attempt(submission):
    if submission.status == "reopened":
        return submission.submission_count + 1
    return submission.submission_count or None


def _unique_assignments(items, key):
    relationship = {"exam_id": "exam", "class_id": "school_class", "subject_id": "subject"}[key]
    unique = {getattr(item, key): getattr(item, relationship) for item in items}
    return sorted(unique.values(), key=lambda item: (getattr(item, "sort_order", 0), item.name.casefold()))


def _result_entry_scope(teacher):
    assignments = (
        TeacherResultAssignment.query
        .options(
            joinedload(TeacherResultAssignment.academic_year),
            joinedload(TeacherResultAssignment.exam),
            joinedload(TeacherResultAssignment.school_class).joinedload(AcademicClass.academic_level),
            joinedload(TeacherResultAssignment.subject),
            joinedload(TeacherResultAssignment.submissions),
        )
        .filter_by(teacher_id=teacher.id, is_active=True)
        .order_by(
            TeacherResultAssignment.deadline_at.is_(None),
            TeacherResultAssignment.deadline_at,
            TeacherResultAssignment.id,
        )
        .all()
    )

    raw_filters = {key: request.args.get(key, type=int) for key in RESULT_ENTRY_FILTER_KEYS if key != "status"}
    raw_filters["status"] = (request.args.get("status") or "all").strip()
    filters = {key: None for key in RESULT_ENTRY_FILTER_KEYS}
    filters["status"] = raw_filters["status"] if raw_filters["status"] in {"all", "not_started", "draft", "submitted", "approved", "reopened"} else "all"

    years = sorted(
        {item.academic_year_id: item.academic_year for item in assignments}.values(),
        key=lambda year: (not year.is_current, year.name),
    )
    if raw_filters["academic_year_id"] in {item.id for item in years}:
        filters["academic_year_id"] = raw_filters["academic_year_id"]
    scoped = [item for item in assignments if not filters["academic_year_id"] or item.academic_year_id == filters["academic_year_id"]]

    exams = _unique_assignments(scoped, "exam_id")
    if raw_filters["exam_id"] in {item.id for item in exams}:
        filters["exam_id"] = raw_filters["exam_id"]
    scoped = [item for item in scoped if not filters["exam_id"] or item.exam_id == filters["exam_id"]]

    levels = sorted(
        {item.school_class.academic_level_id: item.school_class.academic_level for item in scoped}.values(),
        key=lambda level: (level.sort_order, level.name.casefold()),
    )
    if raw_filters["academic_level_id"] in {item.id for item in levels}:
        filters["academic_level_id"] = raw_filters["academic_level_id"]
    scoped = [item for item in scoped if not filters["academic_level_id"] or item.school_class.academic_level_id == filters["academic_level_id"]]

    classes = _unique_assignments(scoped, "class_id")
    if raw_filters["class_id"] in {item.id for item in classes}:
        filters["class_id"] = raw_filters["class_id"]
    scoped = [item for item in scoped if not filters["class_id"] or item.class_id == filters["class_id"]]

    subjects = _unique_assignments(scoped, "subject_id")
    if raw_filters["subject_id"] in {item.id for item in subjects}:
        filters["subject_id"] = raw_filters["subject_id"]
    scoped = [item for item in scoped if not filters["subject_id"] or item.subject_id == filters["subject_id"]]
    status_options = [
        ("all", "Dhammaan xaaladaha"),
        ("not_started", "Aan la bilaabin"),
        ("draft", "Qabyo"),
        ("reopened", "Dib loo furay"),
        ("submitted", "La gudbiyey"),
        ("approved", "La ansixiyey"),
    ]
    if filters["status"] != "all":
        scoped = [
            item for item in scoped
            if (next(iter(item.submissions), None).status if item.submissions else "not_started") == filters["status"]
        ]

    selected_id = request.args.get("assignment_id", type=int)
    assignment = next((item for item in scoped if item.id == selected_id), None)
    if assignment is None and scoped:
        assignment = scoped[0]
    filter_options = {
        "academic_years": years,
        "exams": exams,
        "levels": levels,
        "classes": classes,
        "subjects": subjects,
        "statuses": status_options,
    }
    return assignments, filter_options, filters, scoped, assignment


def _redirect_result_entry(assignment_id, preserve_status=True):
    arguments = {"assignment_id": assignment_id}
    for key in RESULT_ENTRY_FILTER_KEYS:
        if key == "status" and not preserve_status:
            continue
        value = request.form.get(key)
        if value:
            arguments[key] = value
    return redirect(url_for("teacher_portal.result_entry", **arguments))


@teacher_portal_bp.before_request
def protect_teacher_portal():
    if not request.endpoint or not request.endpoint.startswith("teacher_portal."):
        return None
    if request.endpoint in TEACHER_PUBLIC_ENDPOINTS:
        return None
    return None


def _ctx(teacher, filters, page_title, active_endpoint):
    options = filter_options(teacher, filters)
    today = date.today()
    has_club_assignment = ClubTeamLeaderAssignment.query.filter(
        ClubTeamLeaderAssignment.teacher_id == teacher.id,
        ClubTeamLeaderAssignment.start_date <= today,
        db.or_(ClubTeamLeaderAssignment.end_date.is_(None), ClubTeamLeaderAssignment.end_date > today),
    ).first() is not None
    return {
        "teacher": teacher,
        "filters": filters,
        "filter_options": options,
        "page_title": page_title,
        "active_endpoint": active_endpoint,
        "teacher_can": teacher_can,
        "teacher_theme": teacher_theme(),
        "teacher_settings": get_teacher_settings(),
    }


@teacher_portal_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated and is_teacher_account():
        return redirect(safe_teacher_next_url(request.args.get("next")))
    if request.method == "POST":
        session.pop("teacher_login_username", None)
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user, teacher, error = authenticate_teacher_login(username, password)
        if error:
            flash(error, "danger")
            return render_template("teacher/login.html", login_username=username)
        else:
            login_user(user)
            session.pop("admin_last_activity", None)
            session.pop("admin_session_timeout_minutes", None)
            record_teacher_login(teacher)
            db.session.commit()
            return redirect(safe_teacher_next_url(request.args.get("next")))
    login_username = session.pop("teacher_login_username", "")
    return render_template("teacher/login.html", login_username=login_username)


@teacher_portal_bp.route("/logout", methods=["POST"])
@teacher_login_required
def logout():
    teacher = get_current_teacher()
    if teacher and teacher.user:
        session["teacher_login_username"] = teacher.user.username
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
        teacher_id = request.form.get("teacher_id", "").strip()
        request_teacher_password_reset(username, teacher_id)
        db.session.commit()
        flash("Haddii xogtu sax tahay, codsiga dib-u-dejinta waa la diiwaangeliyey. La xiriir maamulka dugsiga.", "success")
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
                log_teacher_activity(teacher, "Password Changed by Teacher", "Macallinku wuxuu beddelay furaha gelitaanka.")
                db.session.commit()
                flash("Password changed successfully.", "success")
                return redirect(safe_teacher_next_url(None))
    return render_template(
        "teacher/change_password.html",
        forced=teacher_requires_password_change(),
        teacher_theme=teacher_theme(),
    )


@teacher_portal_bp.route("/", strict_slashes=False)
def dashboard():
    if not (current_user.is_authenticated and is_teacher_account()):
        return render_template("teacher/login.html", login_username=session.pop("teacher_login_username", ""))
    if teacher_requires_password_change():
        return redirect(url_for("teacher_portal.change_password"))
    return redirect(url_for("teacher_portal.result_entry", **request.args))


@teacher_portal_bp.route("/result-entry")
@teacher_login_required
def result_entry():
    teacher = get_current_teacher()
    assignments, entry_filter_options, filters, visible_assignments, assignment = _result_entry_scope(teacher)
    deadline_labels = {
        item.id: item.deadline_at.replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo("Africa/Mogadishu"))
        for item in assignments if item.deadline_at
    }

    rows = []
    submission = None
    max_score = None
    locked = False
    grade_scales = {"exam": [], "global": [], "fallback": []}
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
                "score": draft.score if draft else (existing.score if existing else None),
                "previous_score": draft.score if draft and draft.score is not None else (existing.score if existing else None),
                "is_existing": bool(existing),
                "has_draft_mark": bool(draft),
                "mark_status": draft.status if draft else "draft",
                "is_locked": bool(draft and draft.status in {"submitted", "approved"}),
                "action_type": (draft.last_action if draft and draft.last_action else None)
                or ("legacy" if (draft.score if draft else (existing.score if existing else None)) is not None else None),
            })
        max_score = resolve_subject_max_score(
            assignment.subject,
            exam=assignment.exam,
            academic_year_level_id=year_level.id if year_level else None,
            academic_level_id=assignment.school_class.academic_level_id,
        )
        marking_configuration = ExamMarkingConfiguration.query.filter_by(
            academic_year_id=assignment.academic_year_id,
            academic_year_level_id=year_level.id,
            exam_id=assignment.exam_id,
        ).first() if year_level else None
        grade_scale_cache = load_grade_scale_cache(
            assignment.exam_id,
            full_marks=marking_configuration.default_full_marks if marking_configuration else None,
        )
        grade_scales = {
            bucket: [
                {
                    "min_score": float(row["min_score"]),
                    "max_score": float(row["max_score"]),
                    **row["payload"],
                }
                for row in grade_scale_cache.get(bucket, [])
            ]
            for bucket in ("exam", "global", "fallback")
        }
        deadline_passed = bool(assignment.deadline_at and datetime.utcnow() > assignment.deadline_at)
        reopened = bool(submission and submission.status == "reopened")
        locked = (deadline_passed and not reopened) or bool(submission and submission.status == "approved")

    approved_mark_count = sum(mark.status == "approved" for mark in submission.marks) if submission else 0
    pending_mark_count = sum(mark.status == "submitted" for mark in submission.marks) if submission else 0

    response = make_response(render_template(
        "teacher/result_entry.html",
        teacher=teacher,
        page_title="Gelinta natiijooyinka",
        active_endpoint="teacher_portal.result_entry",
        teacher_can=teacher_can,
        teacher_theme=teacher_theme(),
        teacher_settings=get_teacher_settings(),
        assignments=assignments,
        visible_assignments=visible_assignments,
        entry_filter_options=entry_filter_options,
        entry_filters=filters,
        deadline_labels=deadline_labels,
        assignment=assignment,
        submission=submission,
        rows=rows,
        max_score=max_score,
        grade_scales=grade_scales,
        locked=locked,
        approved_mark_count=approved_mark_count,
        pending_mark_count=pending_mark_count,
        remaining_submissions=max(0, (assignment.max_submissions or 0) - (submission.submission_count if submission else 0)) if assignment else 0,
        now=datetime.utcnow(),
    ))
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
    response.headers["Pragma"] = "no-cache"
    return response


@teacher_portal_bp.route("/result-entry/autosave", methods=["POST"])
@teacher_login_required
def result_entry_autosave():
    teacher = get_current_teacher()
    payload = request.get_json(silent=True) or {}
    assignment = TeacherResultAssignment.query.filter_by(
        id=payload.get("assignment_id"), teacher_id=teacher.id, is_active=True
    ).first_or_404()
    now = datetime.utcnow()
    submission = TeacherResultSubmission.query.filter_by(assignment_id=assignment.id).first()

    def failure(message, status=400):
        return jsonify(success=False, message=message), status

    if submission and submission.status == "approved":
        return failure("Xaashida oo dhan waa la ansixiyey oo way xiran tahay. Maamulka ayaa dib u furi kara.", 409)
    if assignment.deadline_at and now > assignment.deadline_at and not (submission and submission.status == "reopened"):
        return failure("Waqtigii gudbinta natiijadan wuu dhammaaday.", 409)

    year_level, year_class = _year_scope_ids_from_legacy(
        assignment.academic_year_id,
        assignment.school_class.academic_level_id,
        assignment.class_id,
    )
    if not year_level or not year_class or assignment.exam.academic_year_id != assignment.academic_year_id:
        return failure("Xilsaarista natiijadu hadda ma shaqaynayso. Dib u cusboonaysii bogga.", 409)
    try:
        student_id = int(payload.get("student_id"))
    except (TypeError, ValueError):
        return failure("Ardayga lama aqoonsan. Dib u cusboonaysii bogga.", 400)
    student = students_for_scope_query(
        assignment.academic_year_id,
        level_id=assignment.school_class.academic_level_id,
        class_id=assignment.class_id,
        exam=assignment.exam,
    ).filter(Student.id == student_id).first()
    if not student:
        return failure("Ardaygani kuma jiro fasalka xilsaaran.", 403)

    marks = {mark.student_id: mark for mark in submission.marks} if submission else {}
    mark = marks.get(student.id)
    if mark and mark.status in {"submitted", "approved"}:
        return failure("Dhibcahan hore ayaa maamulka loogu gudbiyey, mana la beddeli karo.", 409)

    raw_score = payload.get("score", "")
    raw_score = "" if raw_score is None else str(raw_score).strip()
    score = None
    if raw_score:
        configured_maximum = resolve_subject_max_score(
            assignment.subject,
            exam=assignment.exam,
            academic_year_level_id=year_level.id,
            academic_level_id=assignment.school_class.academic_level_id,
        )
        if configured_maximum is None:
            return failure("Xadka dhibcaha maaddadan lama dejin. La xiriir maamulka.", 409)
        score, score_error = _parse_result_entry_score(raw_score)
        if score_error == "precision":
            return failure("Geli dhibcaha ugu badnaan laba meelood oo jajab ah.", 422)
        if score_error:
            return failure("Darajada ma saxna. Geli nambar sax ah.", 422)
        maximum = Decimal(str(configured_maximum))
        if score > maximum:
            return failure("Darajadu waxay dhaaftay %s. Hubi nambarka." % maximum, 422)

    official = Result.query.filter_by(
        exam_id=assignment.exam_id, subject_id=assignment.subject_id, student_id=student.id
    ).first()
    if not raw_score:
        if mark and mark.status == "draft":
            previous_score = mark.score
            had_previous_action = bool(mark.last_action)
            action_type = _set_result_mark_action(mark, previous_score, None, now)
            if action_type:
                record_teacher_result_mark_revision(
                    mark, previous_score, None, action_type,
                    is_change=previous_score is not None or had_previous_action,
                    entry_status="draft",
                    submission_attempt=_next_result_submission_attempt(submission),
                )
            mark.score = None
            db.session.commit()
        display_score = mark.score if mark else (official.score if official else None)
        return jsonify(
            success=True,
            student_id=student.id,
            score=str(display_score) if display_score is not None else None,
            mark_status=mark.status if mark else "draft",
            has_draft=bool(mark),
            action_type=(mark.last_action if mark and mark.last_action else None)
            or ("legacy" if display_score is not None else None),
            saved_at=now.isoformat(),
        )

    if not submission:
        submission = TeacherResultSubmission(assignment=assignment)
        db.session.add(submission)
        db.session.flush()
    previous_score = mark.score if mark else (official.score if official else None)
    had_previous_action = bool(mark and mark.last_action)
    if not mark:
        mark = TeacherResultMark(submission=submission, student_id=student.id)
        db.session.add(mark)
    action_type = _set_result_mark_action(mark, previous_score, score, now)
    mark.score = score
    mark.status = "draft"
    if submission.status not in {"submitted", "reopened"}:
        submission.status = "draft"
    if action_type:
        record_teacher_result_mark_revision(
            mark, previous_score, score, action_type,
            is_change=previous_score is not None or had_previous_action,
            entry_status="draft",
            submission_attempt=_next_result_submission_attempt(submission),
        )
    db.session.commit()
    return jsonify(
        success=True,
        student_id=student.id,
        score=str(score),
        mark_status=mark.status,
        has_draft=True,
        action_type=mark.last_action or "legacy",
        saved_at=now.isoformat(),
    )


@teacher_portal_bp.route("/result-entry/save", methods=["POST"])
@teacher_login_required
def result_entry_save():
    teacher = get_current_teacher()
    wants_json = request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.accept_mimetypes.best == "application/json"

    def failure(message, category="danger", status=422):
        if wants_json:
            return jsonify(success=False, message=message), status
        flash(message, category)
        return _redirect_result_entry(assignment.id)

    assignment = TeacherResultAssignment.query.filter_by(
        id=request.form.get("assignment_id", type=int), teacher_id=teacher.id, is_active=True
    ).first_or_404()
    now = datetime.utcnow()
    submission = TeacherResultSubmission.query.filter_by(assignment_id=assignment.id).first()
    action = request.form.get("action")
    if submission and submission.status == "approved":
        return failure("Xaashida oo dhan waa la ansixiyey oo way xiran tahay. Maamulka ayaa dib u furi kara.", "warning", 409)
    if assignment.deadline_at and now > assignment.deadline_at and not (submission and submission.status == "reopened"):
        return failure("Waqtigii gudbinta natiijadan wuu dhammaaday.", "danger", 409)
    if action == "submit" and submission and submission.status == "reopened" and submission.submission_count >= assignment.max_submissions:
        return failure("Tirada gudbinta ee laguu oggolaaday way dhammaatay. La xiriir maamulka.", "danger", 409)

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
    official_scores = {
        result.student_id: result.score
        for result in Result.query.filter_by(exam_id=assignment.exam_id, subject_id=assignment.subject_id)
        .filter(Result.student_id.in_([student.id for student in students])).all()
    } if students else {}
    max_score = Decimal(str(resolve_subject_max_score(
        assignment.subject,
        exam=assignment.exam,
        academic_year_level_id=year_level.id,
        academic_level_id=assignment.school_class.academic_level_id,
    )))
    errors = []
    precision_errors = []
    submit_now = action == "submit"
    existing_marks = {
        mark.student_id: mark for mark in submission.marks
    } if submission else {}
    previous_submission_status = submission.status if submission else None
    revision_changes = []
    score_by_student = {}
    for student in students:
        mark = existing_marks.get(student.id)
        if mark and mark.status in {"submitted", "approved"}:
            continue
        raw = (request.form.get(f"score_{student.id}") or "").strip()
        if not raw:
            continue
        score, score_error = _parse_result_entry_score(raw)
        if score_error == "precision":
            precision_errors.append(student.full_name)
            continue
        if score_error:
            errors.append(student.full_name)
            continue
        if score > max_score:
            errors.append(student.full_name)
            continue
        score_by_student[student.id] = score
    if precision_errors:
        return failure("Geli ugu badnaan laba meelood oo jajab ah: %s" % ", ".join(precision_errors[:6]))
    if errors:
        return failure("Dhibcaha ardaydan kama dhex jiraan 0 iyo %s: %s" % (max_score, ", ".join(errors[:6])))

    if not submission:
        submission = TeacherResultSubmission(assignment=assignment)
        db.session.add(submission)
        db.session.flush()
    mark_by_student = {mark.student_id: mark for mark in submission.marks}
    if not submit_now:
        for student in students:
            existing_mark = mark_by_student.get(student.id)
            if existing_mark and existing_mark.status == "draft" and not (request.form.get(f"score_{student.id}") or "").strip():
                previous_score = existing_mark.score
                had_previous_action = bool(existing_mark.last_action)
                action_type = _set_result_mark_action(existing_mark, previous_score, None, now)
                if action_type:
                    revision_changes.append((
                        existing_mark, previous_score, None, action_type,
                        previous_score is not None or had_previous_action,
                    ))
                existing_mark.score = None
    for student_id, score in score_by_student.items():
        mark = mark_by_student.get(student_id)
        previous_score = mark.score if mark else official_scores.get(student_id)
        had_previous_action = bool(mark and mark.last_action)
        if not mark:
            mark = TeacherResultMark(submission=submission, student_id=student_id)
            db.session.add(mark)
        action_type = _set_result_mark_action(mark, previous_score, score, now)
        if action_type:
            revision_changes.append((
                mark, previous_score, score, action_type,
                previous_score is not None or had_previous_action,
            ))
        mark.score = score
        mark.status = "submitted" if submit_now else "draft"
    submit_message = None
    if submit_now:
        pending_marks = [mark for mark in submission.marks if mark.status == "submitted" and mark.score is not None]
        if not pending_marks:
            db.session.rollback()
            return failure("Geli ugu yaraan hal dhibcood oo cusub ka hor intaadan maamulka u gudbin.", "warning", 422)
        reopened = submission.status == "reopened"
        if reopened:
            if submission.submission_count >= assignment.max_submissions:
                db.session.rollback()
                return failure("Tirada gudbinta ee laguu oggolaaday way dhammaatay.", "danger", 409)
            submission.submission_count += 1
        elif submission.submission_count == 0:
            submission.submission_count = 1
        submission.status = "submitted"
        submission.submitted_at = now
        record_teacher_result_event(
            assignment,
            "resubmitted" if reopened else "submitted",
            submission=submission,
            status_from="reopened" if reopened else previous_submission_status or "draft",
            status_to="submitted",
            marks_count=len(pending_marks),
            note=f"{len(pending_marks)} arday ayaa lagu gudbiyey isku-dayga {submission.submission_count}.",
        )
        submit_message = "Dhibcaha %s arday ayaa maamulka loo gudbiyey si dib loogu eego." % len(score_by_student)
    else:
        if submission.status not in {"submitted", "reopened"}:
            submission.status = "draft"
        record_teacher_result_event(
            assignment,
            "draft_saved",
            submission=submission,
            status_from=previous_submission_status,
            status_to=submission.status,
            marks_count=sum(mark.score is not None and mark.status == "draft" for mark in submission.marks),
            note="Qabyada dhibcaha waa la kaydiyey.",
        )
        submit_message = "Qabyada natiijada waa la kaydiyey."
    for mark, previous_score, next_score, action_type, is_change in revision_changes:
        record_teacher_result_mark_revision(
            mark, previous_score, next_score, action_type,
            is_change=is_change,
            entry_status="submitted" if submit_now else "draft",
            submission_attempt=(
                submission.submission_count
                if submit_now else _next_result_submission_attempt(submission)
            ),
        )
    db.session.commit()
    if wants_json:
        response = _redirect_result_entry(assignment.id, preserve_status=False)
        return jsonify(
            success=True,
            message=submit_message,
            redirect_url=response.headers.get("Location"),
        )
    flash(submit_message, "success")
    return _redirect_result_entry(assignment.id, preserve_status=False)


@teacher_portal_bp.route("/clubs")
@teacher_login_required
def clubs():
    """Show only the current club assignments and rosters of this leader."""
    teacher = get_current_teacher()
    today = date.today()
    assignments = ClubTeamLeaderAssignment.query.filter(
        ClubTeamLeaderAssignment.teacher_id == teacher.id,
        ClubTeamLeaderAssignment.start_date <= today,
        db.or_(ClubTeamLeaderAssignment.end_date.is_(None), ClubTeamLeaderAssignment.end_date > today),
    ).order_by(ClubTeamLeaderAssignment.start_date.desc()).all()
    teams = []
    for assignment in assignments:
        memberships = ClubTeamMembership.query.filter(
            ClubTeamMembership.team_id == assignment.team_id,
            ClubTeamMembership.start_date <= today,
            db.or_(ClubTeamMembership.end_date.is_(None), ClubTeamMembership.end_date > today),
        ).order_by(ClubTeamMembership.id).all()
        teams.append({"assignment": assignment, "memberships": memberships})
    record_teacher_visit(teacher, "My Club Teams")
    ctx = _ctx(teacher, {}, "Kooxaha Naadiga", "teacher_portal.clubs")
    ctx["teams"] = teams
    db.session.commit()
    return render_template("teacher/clubs.html", **ctx)


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
