from datetime import date, datetime
from tempfile import NamedTemporaryFile
from zoneinfo import ZoneInfo

from flask import Blueprint, abort, flash, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required
from openpyxl import Workbook
from sqlalchemy.orm import selectinload

from . import db
from .audit import audit
from .cloudinary_service import upload_image
from .models import AcademicClass, AcademicLevel, AcademicSection, AcademicYear, AcademicYearClass, AcademicYearLevel, AcademicYearSubject, ClubTeam, ClubTeamLeaderAssignment, ClubTeamMembership, Exam, Result, Setting, Student, StudentClub, StudentEnrollment, Subject, Teacher, TeacherActivity, TeacherResultAssignment, TeacherResultMark, TeacherResultSubmission, User
from .permissions import enforce_endpoint_permission
from .security import ALLOWED_PHOTOS, allowed_file
from .teacher_services import (
    EMPLOYMENT_TYPES,
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


def _teacher_profile_scopes(teacher):
    """Build teacher profile choices from the current year's configured catalog."""
    years = AcademicYear.query.order_by(AcademicYear.is_current.desc(), AcademicYear.name.desc()).all()
    year = next((item for item in years if item.is_current), years[0] if years else None)
    year_levels = []
    if year:
        year_levels = (
            AcademicYearLevel.query
            .filter_by(academic_year_id=year.id, is_active=True)
            .order_by(AcademicYearLevel.sort_order, AcademicYearLevel.name)
            .all()
        )
        year_levels = [
            item for item in year_levels
            if item.legacy_level and item.legacy_level.is_active
        ]

    groups = []
    current_level_ids = set()
    current_class_ids = set()
    current_section_ids = set()
    current_subject_ids = set()
    level_groups = {}

    for year_level in year_levels:
        level = year_level.legacy_level
        level_id = level.id
        current_level_ids.add(level_id)
        group = level_groups.get(level_id)
        if group is None:
            group = {
                "level": level,
                "label": year_level.name,
                "is_legacy": False,
                "classes": [],
                "subjects": [],
            }
            level_groups[level_id] = group
            groups.append(group)

        year_classes = (
            AcademicYearClass.query
            .filter_by(academic_year_level_id=year_level.id, is_active=True)
            .order_by(AcademicYearClass.sort_order, AcademicYearClass.name)
            .all()
        )
        for year_class in year_classes:
            school_class = year_class.legacy_class
            assignable = bool(
                school_class and school_class.is_active
                and school_class.academic_level_id == level_id
            )
            if assignable:
                current_class_ids.add(school_class.id)
            linked_sections = [
                section for section in teacher.sections
                if assignable and section.academic_class_id == school_class.id
            ]
            sections = (
                AcademicSection.query
                .filter(AcademicSection.academic_class_id == school_class.id)
                .filter(db.or_(AcademicSection.is_active.is_(True), AcademicSection.id.in_([s.id for s in linked_sections])))
                .order_by(AcademicSection.sort_order, AcademicSection.name)
                .all()
            ) if assignable else []
            current_section_ids.update(section.id for section in sections if section.is_active)
            group["classes"].append({
                "record": school_class,
                "label": year_class.name,
                "sections": sections,
                "assignable": assignable,
                "is_legacy": False,
            })

        year_subjects = (
            AcademicYearSubject.query
            .filter_by(
                academic_year_id=year.id,
                academic_year_level_id=year_level.id,
                is_active=True,
                subject_kind="exam",
            )
            .order_by(AcademicYearSubject.sort_order, AcademicYearSubject.name)
            .all()
        )
        for year_subject in year_subjects:
            subject = year_subject.legacy_subject
            assignable = bool(
                subject and subject.is_active
                and (subject.academic_level_id is None or subject.academic_level_id == level_id)
            )
            if assignable:
                current_subject_ids.add(subject.id)
            group["subjects"].append({
                "record": subject,
                "label": year_subject.name,
                "assignable": assignable,
                "is_legacy": False,
            })

    linked_level_ids = {item.id for item in teacher.academic_levels}
    linked_class_ids = {item.id for item in teacher.classes}
    linked_section_ids = {item.id for item in teacher.sections}
    linked_subject_ids = {item.id for item in teacher.subjects}
    linked_level_ids.update(item.academic_level_id for item in teacher.classes)
    linked_level_ids.update(item.academic_level_id for item in teacher.subjects if item.academic_level_id)

    # Keep explicitly assigned historic scopes visible while excluding unrelated legacy catalog rows.
    for group in groups:
        level_id = group["level"].id
        known_class_ids = {
            item["record"].id for item in group["classes"] if item["record"]
        }
        for school_class in teacher.classes:
            if school_class.academic_level_id == level_id and school_class.id not in known_class_ids:
                group["classes"].append({
                    "record": school_class,
                    "label": school_class.name,
                    "sections": [section for section in teacher.sections if section.academic_class_id == school_class.id],
                    "assignable": True,
                    "is_legacy": True,
                })
        known_subject_ids = {
            item["record"].id for item in group["subjects"] if item["record"]
        }
        for subject in teacher.subjects:
            if subject.academic_level_id == level_id and subject.id not in known_subject_ids:
                group["subjects"].append({
                    "record": subject,
                    "label": subject.name,
                    "assignable": True,
                    "is_legacy": True,
                })

    historic_level_ids = linked_level_ids - current_level_ids
    for level_id in sorted(historic_level_ids):
        level = db.session.get(AcademicLevel, level_id)
        if not level:
            continue
        historic_classes = [item for item in teacher.classes if item.academic_level_id == level_id]
        historic_subjects = [item for item in teacher.subjects if item.academic_level_id == level_id]
        if not historic_classes and not historic_subjects and level not in teacher.academic_levels:
            continue
        groups.append({
            "level": level,
            "label": level.name,
            "is_legacy": True,
            "classes": [{
                "record": school_class,
                "label": school_class.name,
                "sections": [section for section in teacher.sections if section.academic_class_id == school_class.id],
                "assignable": True,
                "is_legacy": True,
            } for school_class in historic_classes],
            "subjects": [{
                "record": subject,
                "label": subject.name,
                "assignable": True,
                "is_legacy": True,
            } for subject in historic_subjects],
        })

    current_subject_ids.update(
        item["record"].id
        for group in groups if not group["is_legacy"]
        for item in group["subjects"]
        if item["record"] and item["assignable"] and not item["is_legacy"]
    )
    shared_subjects = [
        subject for subject in teacher.subjects
        if subject.academic_level_id is None and subject.id not in current_subject_ids
    ]
    return {
        "year": year,
        "groups": groups,
        "shared_subjects": shared_subjects,
        "linked_level_ids": linked_level_ids,
        "linked_class_ids": linked_class_ids,
        "linked_section_ids": linked_section_ids,
        "linked_subject_ids": linked_subject_ids,
        "current_level_ids": current_level_ids,
        "current_class_ids": current_class_ids,
        "current_section_ids": current_section_ids,
        "current_subject_ids": current_subject_ids,
    }


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
            reopen_status = "submitted" if submission.status == "submitted" else "approved"
            submission.status = "reopened"
            for mark in submission.marks:
                if mark.status == reopen_status:
                    mark.status = "draft"
            assignment.max_submissions = max(assignment.max_submissions, submission.submission_count + 1)
            submission.reviewer_note = reviewer_note[:500]
        elif action == "approve":
            if submission.status != "submitted":
                flash("Submission-ku waa inuu ku jiraa xaaladda dib-u-eegis.", "warning")
                return redirect(url_for("admin_teachers.result_entry_setup"))
            pending_marks = [
                mark for mark in submission.marks
                if mark.status == "submitted" and mark.score is not None
            ]
            if not pending_marks:
                flash("Ma jiraan dhibco cusub oo sugaya ansixin.", "warning")
                return redirect(url_for("admin_teachers.result_entry_setup"))
            marks = {mark.student_id: mark for mark in pending_marks}
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
            roster_ids = {student.id for student in students}
            if not year_level or not year_class or any(student_id not in roster_ids for student_id in marks):
                flash("Xogta ardayda ama fasalka/sanadku sax ma aha; lama ansixin.", "danger")
                return redirect(url_for("admin_teachers.result_entry_setup"))
            pending_students = [student for student in students if student.id in marks]
            if any(student.is_result_locked for student in pending_students):
                flash("Fasalka waxaa ku jira arday natiijadoodu xiran tahay. Furitaanka natiijooyinkaas ayaa loo baahan yahay ka hor ansixinta.", "danger")
                return redirect(url_for("admin_teachers.result_entry_setup"))
            maximum = resolve_subject_max_score(
                assignment.subject,
                exam=assignment.exam,
                academic_year_level_id=year_level.id,
                academic_level_id=assignment.school_class.academic_level_id,
            )
            if any(mark.score < 0 or mark.score > maximum for mark in pending_marks):
                flash("Dhibco ka baxsan xadka maaddada ayaa jira; submission-ka lama ansixin.", "danger")
                return redirect(url_for("admin_teachers.result_entry_setup"))
            pending_ids = [student.id for student in pending_students]
            existing_result_count = Result.query.filter(
                Result.exam_id == assignment.exam_id,
                Result.subject_id == assignment.subject_id,
                Result.student_id.in_(pending_ids),
            ).count() if pending_ids else 0
            if existing_result_count and request.form.get("confirm_replace") != "yes":
                flash("Dhibco rasmi ah ayaa hore u jira. Isbarbar dhig kadib calaamadee sanduuqa si si cad loogu oggolaado beddeliddooda.", "warning")
                return redirect(url_for("admin_teachers.result_entry_setup"))
            for student in pending_students:
                result = Result.query.filter_by(
                    student_id=student.id, exam_id=assignment.exam_id, subject_id=assignment.subject_id
                ).first()
                if result is None:
                    result = Result(student_id=student.id, exam_id=assignment.exam_id, subject_id=assignment.subject_id, score=marks[student.id].score)
                    db.session.add(result)
                else:
                    result.score = marks[student.id].score
                result.is_published = True
                marks[student.id].status = "approved"
            already_published_ids = {
                student_id for (student_id,) in db.session.query(Result.student_id).filter(
                    Result.exam_id == assignment.exam_id,
                    Result.subject_id == assignment.subject_id,
                    Result.is_published.is_(True),
                    Result.student_id.in_(roster_ids),
                ).all()
            } if roster_ids else set()
            complete_roster = roster_ids.issubset(already_published_ids | set(pending_ids))
            submission.status = "approved" if complete_roster else "draft"
            submission.reviewed_at = datetime.utcnow()
            submission.reviewed_by_id = current_user.id
            submission.reviewer_note = (request.form.get("reviewer_note") or "Approved and published.").strip()[:500]
            audit("Teacher Result Approval", f"Approved submission {submission.id} ({len(pending_marks)} submitted marks; {existing_result_count} existing scores replaced): {assignment.exam.name} / {assignment.school_class.name} / {assignment.subject.name}")
            if complete_roster:
                flash("Dhammaan dhibcaha xaashidan waa la ansixiyey oo la daabacay.", "success")
            else:
                flash("Qaybta dhibcaha la gudbiyey waa la ansixiyey; inta harsan macallinku wuu sii gelin karaa.", "success")
        else:
            abort(400)
        db.session.commit()
        if action != "approve":
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
    assignment_progress = {}
    for assignment in assignments:
        submission = next(iter(assignment.submissions), None)
        if not submission:
            continue
        assignment_progress[assignment.id] = {
            "approved": sum(mark.status == "approved" for mark in submission.marks),
            "pending": sum(mark.status == "submitted" for mark in submission.marks),
            "draft": sum(mark.status == "draft" and mark.score is not None for mark in submission.marks),
        }
        if submission.status != "submitted":
            continue
        submitted_marks = [mark for mark in submission.marks if mark.status == "submitted" and mark.score is not None]
        student_ids = [mark.student_id for mark in submitted_marks]
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
            for mark in submitted_marks
        ]
    deadline_labels = {
        item.id: item.deadline_at.replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo("Africa/Mogadishu"))
        for item in assignments if item.deadline_at
    }
    teachers = Teacher.query.filter_by(is_active=True, employment_status="Active").order_by(Teacher.full_name).all()
    unlinked_staff_users = (
        User.query
        .filter(User.role == "staff", ~User.teacher_profile.has())
        .order_by(User.full_name, User.username)
        .all()
    )
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
        assignments=assignments, teachers=teachers, unlinked_staff_users=unlinked_staff_users,
        years=years, exams=exams,
        year_levels=year_levels, year_classes=year_classes, year_subjects=year_subjects,
        selected_year_id=selected_year_id, selected_year_level_id=selected_year_level_id,
        assignment_scope_details=assignment_scope_details,
        deadline_labels=deadline_labels,
        review_rows=review_rows,
        assignment_progress=assignment_progress,
        assignments_with_existing_results=assignments_with_existing_results,
        summary=summary, status_filter=status_filter, teacher_filter=teacher_filter,
    )


@teachers_bp.route("/list")
def teacher_list():
    filters = {
        "q": request.args.get("q", "").strip(),
        "department": request.args.get("department", "").strip(),
        "academic_level_id": request.args.get("academic_level_id", "").strip(),
        "employment_status": request.args.get("employment_status", "").strip(),
        "subject_id": request.args.get("subject_id", "").strip(),
        "class_id": request.args.get("class_id", "").strip(),
        "account_status": request.args.get("account_status", "").strip(),
    }
    page = max(request.args.get("page", 1, type=int), 1)
    per_page = 15
    pagination = teacher_list_query(filters).options(
        selectinload(Teacher.user),
        selectinload(Teacher.academic_levels),
        selectinload(Teacher.classes).joinedload(AcademicClass.academic_level),
        selectinload(Teacher.subjects),
    ).paginate(page=page, per_page=per_page, error_out=False)
    departments = [
        row[0]
        for row in db.session.query(Teacher.department).filter(Teacher.department.isnot(None), Teacher.department != "").distinct().order_by(Teacher.department).all()
    ]
    teacher_scope_names = {}
    for item in pagination.items:
        levels_by_id = {level.id: level.name for level in item.academic_levels}
        levels_by_id.update({school_class.academic_level.id: school_class.academic_level.name for school_class in item.classes if school_class.academic_level})
        teacher_scope_names[item.id] = {
            "levels": sorted(levels_by_id.values(), key=str.casefold),
            "classes": [f"{school_class.name}" for school_class in sorted(item.classes, key=lambda value: (value.academic_level.sort_order, value.sort_order, value.name.casefold()))],
        }
    return render_template(
        "admin/teachers/list.html",
        teachers=pagination.items,
        pagination=pagination,
        filters=filters,
        teacher_scope_names=teacher_scope_names,
        departments=departments,
        subjects=Subject.query.filter_by(is_active=True).order_by(Subject.sort_order, Subject.name).all(),
        classes=AcademicClass.query.filter_by(is_active=True).order_by(AcademicClass.academic_level_id, AcademicClass.sort_order, AcademicClass.name).all(),
        academic_levels=AcademicLevel.query.filter_by(is_active=True).order_by(AcademicLevel.sort_order, AcademicLevel.name).all(),
        account_summary={
            "total": Teacher.query.count(),
            "linked": Teacher.query.filter(Teacher.user_id.isnot(None)).count(),
            "enabled": Teacher.query.filter(
                Teacher.is_active.is_(True),
                Teacher.employment_status == "Active",
                Teacher.user.has(User.is_active.is_(True)),
            ).count(),
            "missing": Teacher.query.filter(Teacher.user_id.is_(None)).count(),
        },
    )


@teachers_bp.route("/new", methods=["GET", "POST"])
@teachers_bp.route("/<int:teacher_id>/edit", methods=["GET", "POST"])
def teacher_form(teacher_id=None):
    teacher = (db.session.get(Teacher, teacher_id) or abort(404)) if teacher_id else Teacher()
    settings = get_teacher_settings()
    scope_data = _teacher_profile_scopes(teacher)
    selected_portal_user_id = request.args.get("user_id", type=int)
    if request.method == "POST":
        selected_portal_user_id = request.form.get("existing_user_id", type=int)
    linkable_staff_users = (
        User.query
        .filter(User.role == "staff", ~User.teacher_profile.has())
        .order_by(User.full_name, User.username)
        .all()
    ) if not teacher.user_id else []
    selected_portal_user = next(
        (item for item in linkable_staff_users if item.id == selected_portal_user_id), None
    )
    if selected_portal_user and not teacher.id:
        teacher.full_name = teacher.full_name or selected_portal_user.full_name

    if request.method == "POST":
        try:
            if selected_portal_user_id:
                if teacher.user_id:
                    raise ValueError("Macallinkan akoon ayuu hore ugu xiran yahay; akoon kale laguma beddeli karo form-kan.")
                selected_portal_user = db.session.get(User, selected_portal_user_id)
                linked_profile = Teacher.query.filter_by(user_id=selected_portal_user_id).first()
                if (
                    not selected_portal_user
                    or selected_portal_user.role != "staff"
                    or (linked_profile and linked_profile.id != teacher.id)
                ):
                    raise ValueError("Akoonka la doortay ma aha staff aan macallin kale ku xirnayn.")
                teacher.user_id = selected_portal_user.id

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
            teacher.is_active = teacher.employment_status == "Active"

            photo = request.files.get("photo")
            if photo and photo.filename:
                if not allowed_file(photo.filename, ALLOWED_PHOTOS):
                    raise ValueError("Photo must be JPG, PNG, or WEBP.")
                teacher.photo_path = upload_image(photo, "school/teachers")

            def posted_ids(field):
                values = request.form.getlist(field)
                if any(not value.isdigit() or int(value) < 1 for value in values):
                    raise ValueError("Xulashada heerka, fasalka, qaybta ama maaddadu sax ma aha.")
                return list(dict.fromkeys(int(value) for value in values))

            def selected_records(model, ids, existing_ids):
                records = model.query.filter(model.id.in_(ids)).all() if ids else []
                if len(records) != len(ids) or any(
                    not record.is_active and record.id not in existing_ids for record in records
                ):
                    raise ValueError("Waxaa la doortay xog aan jirin ama aan hadda firfircoonayn.")
                return records

            existing_level_ids = scope_data["linked_level_ids"]
            existing_class_ids = scope_data["linked_class_ids"]
            existing_section_ids = scope_data["linked_section_ids"]
            existing_subject_ids = scope_data["linked_subject_ids"]
            selected_level_ids = posted_ids("academic_level_ids")
            selected_class_ids = posted_ids("academic_class_ids")
            selected_section_ids = posted_ids("academic_section_ids")
            selected_subject_ids = posted_ids("subject_ids")

            allowed_ids = (
                (selected_level_ids, scope_data["current_level_ids"] | existing_level_ids),
                (selected_class_ids, scope_data["current_class_ids"] | existing_class_ids),
                (selected_section_ids, scope_data["current_section_ids"] | existing_section_ids),
                (selected_subject_ids, scope_data["current_subject_ids"] | existing_subject_ids),
            )
            if any(set(ids) - allowed for ids, allowed in allowed_ids):
                raise ValueError("Heer, fasal, qayb ama maaddo laguma diiwaangelin qaab-dhismeedka sanad-dugsiyeedka hadda.")

            selected_levels = selected_records(AcademicLevel, selected_level_ids, existing_level_ids)
            selected_classes = selected_records(AcademicClass, selected_class_ids, existing_class_ids)
            selected_sections = selected_records(AcademicSection, selected_section_ids, existing_section_ids)
            selected_subjects = selected_records(Subject, selected_subject_ids, existing_subject_ids)
            selected_class_id_set = {item.id for item in selected_classes}
            if any(section.academic_class_id not in selected_class_id_set for section in selected_sections):
                raise ValueError("Qaybta ardaydu waxay u baahan tahay in fasalkeeda la doorto.")

            level_ids = {item.id for item in selected_levels}
            level_ids.update(item.academic_level_id for item in selected_classes)
            level_ids.update(item.academic_level_id for item in selected_subjects if item.academic_level_id)
            teacher.academic_levels = AcademicLevel.query.filter(AcademicLevel.id.in_(level_ids)).all() if level_ids else []
            teacher.classes = selected_classes
            teacher.sections = selected_sections
            teacher.subjects = selected_subjects

            db.session.add(teacher)
            db.session.flush()

            username = request.form.get("username", "").strip() or default_teacher_username(teacher)
            password = request.form.get("password", "")
            confirm = request.form.get("confirm_password", "")
            create_account = bool(request.form.get("create_account"))
            if selected_portal_user_id or create_account or teacher.user_id:
                if password and password != confirm:
                    raise ValueError("Passwords do not match.")
                create_or_update_teacher_user(
                    teacher,
                    username=username,
                    password=password,
                    account_active=request.form.get("account_active") == "on",
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
        current_academic_year=scope_data["year"],
        scope_groups=scope_data["groups"],
        shared_subjects=scope_data["shared_subjects"],
        linked_level_ids=scope_data["linked_level_ids"],
        settings=settings,
        employment_types=EMPLOYMENT_TYPES,
        default_username=default_teacher_username(teacher) if teacher.teacher_id else "",
        linkable_staff_users=linkable_staff_users,
        selected_portal_user=selected_portal_user,
        selected_portal_user_id=selected_portal_user_id,
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
    filters = {
        "q": request.args.get("q", "").strip(),
        "department": request.args.get("department", "").strip(),
        "academic_level_id": request.args.get("academic_level_id", "").strip(),
        "employment_status": request.args.get("employment_status", "").strip(),
        "subject_id": request.args.get("subject_id", "").strip(),
        "class_id": request.args.get("class_id", "").strip(),
        "account_status": request.args.get("account_status", "").strip(),
    }
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
    for teacher in teacher_list_query(filters).all():
        ws.append(
            [
                teacher.teacher_id,
                teacher.teacher_code,
                teacher.full_name,
                teacher.phone,
                teacher.email,
                teacher.department,
                ", ".join(sorted({level.name for level in teacher.academic_levels} | {item.academic_level.name for item in teacher.classes if item.academic_level})),
                ", ".join(subject.name for subject in teacher.subjects),
                ", ".join(school_class.name for school_class in teacher.classes),
                teacher.employment_status,
                "Gelitaan shaqaynaya" if teacher.user and teacher.user.is_active and teacher.is_active and teacher.employment_status == "Active" else ("Gelitaan xiran" if teacher.user else "Akoon ma jiro"),
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
        "academic_level_id": request.args.get("academic_level_id", "").strip(),
        "employment_status": request.args.get("employment_status", "").strip(),
        "subject_id": request.args.get("subject_id", "").strip(),
        "class_id": request.args.get("class_id", "").strip(),
        "account_status": request.args.get("account_status", "").strip(),
    }
    teachers = teacher_list_query(filters).all()
    return render_template("admin/teachers/print.html", teachers=teachers, generated_at=datetime.now())


@teachers_bp.route("/club-teams", methods=["GET", "POST"])
def club_teams():
    """Administer mixed-class club cohorts, rosters, and dated leader rotations."""
    if request.method == "POST":
        operation = request.form.get("operation", "")
        try:
            if operation == "create_club":
                name = request.form.get("name", "").strip()
                if not name:
                    raise ValueError("Magaca naadigu waa waajib.")
                if StudentClub.query.filter(db.func.lower(StudentClub.name) == name.lower()).first():
                    raise ValueError("Naadi magacan leh hore ayuu u jiraa.")
                db.session.add(StudentClub(name=name, description=request.form.get("description", "").strip() or None))
                audit("Club Teams", f"Created club {name}")
                flash("Naadiga waa la abuuray.", "success")
            elif operation == "create_team":
                year = db.session.get(AcademicYear, request.form.get("academic_year_id", type=int))
                name = request.form.get("name", "").strip()
                if not year or not name:
                    raise ValueError("Dooro sannad-dugsiyeed, kuna qor magaca kooxda.")
                if ClubTeam.query.filter_by(academic_year_id=year.id, name=name).first():
                    raise ValueError("Koox magacan leh sannad-dugsiyeedkan way jirtaa.")
                db.session.add(ClubTeam(academic_year=year, name=name))
                audit("Club Teams", f"Created team {name} for {year.name}")
                flash("Kooxda waa la abuuray.", "success")
            elif operation == "add_member":
                team = db.session.get(ClubTeam, request.form.get("team_id", type=int))
                enrollment = db.session.get(StudentEnrollment, request.form.get("enrollment_id", type=int))
                start_date = date.fromisoformat(request.form.get("start_date", ""))
                if not team or not enrollment or enrollment.academic_year_id != team.academic_year_id:
                    raise ValueError("Ardayga iyo kooxdu waa inay ka wada tirsanaadaan isla sannad-dugsiyeedka.")
                if start_date < date.today():
                    raise ValueError("Xubinnimada cusub dib looguma qori karo taariikh hore.")
                if enrollment.status != "active":
                    raise ValueError("Kaliya ardayda firfircoon ayaa lagu dari karaa kooxda.")
                overlapping = ClubTeamMembership.query.filter(
                    ClubTeamMembership.enrollment_id == enrollment.id,
                    db.or_(ClubTeamMembership.end_date.is_(None), ClubTeamMembership.end_date > start_date),
                ).order_by(ClubTeamMembership.start_date.desc()).first()
                if overlapping:
                    if overlapping.team_id == team.id:
                        raise ValueError("Ardaygani kooxdaas ayuu hore ugu jiraa.")
                    if start_date < overlapping.start_date:
                        raise ValueError("Wareejintu kama hor mari karto xubinnimada firfircoon ee hadda jirta.")
                    overlapping.end_date = start_date
                future_membership = ClubTeamMembership.query.filter(
                    ClubTeamMembership.enrollment_id == enrollment.id,
                    ClubTeamMembership.start_date > start_date,
                    db.or_(ClubTeamMembership.end_date.is_(None), ClubTeamMembership.end_date > start_date),
                ).first()
                if future_membership:
                    raise ValueError("Ardaygan wuxuu leeyahay wareejin mustaqbal ah; marka hore dib u eeg taariikhdaas.")
                db.session.add(ClubTeamMembership(
                    team=team, enrollment=enrollment, start_date=start_date,
                    assigned_by_user_id=getattr(current_user, "id", None),
                ))
                audit("Club Teams", f"Assigned enrollment {enrollment.id} to {team.name} effective {start_date.isoformat()}")
                flash("Xubinta kooxda waa la kaydiyey.", "success")
            elif operation == "assign_leader":
                team = db.session.get(ClubTeam, request.form.get("team_id", type=int))
                club = db.session.get(StudentClub, request.form.get("club_id", type=int))
                teacher = db.session.get(Teacher, request.form.get("teacher_id", type=int))
                start_date = date.fromisoformat(request.form.get("start_date", ""))
                if not team or not club or not club.is_active or not teacher or not teacher.is_active or teacher.employment_status != "Active":
                    raise ValueError("Koox, naadi firfircoon, iyo hoggaamiye firfircoon dooro.")
                if not teacher.user_id or not teacher.user or not teacher.user.is_active:
                    raise ValueError("Hoggaamiyahan weli akoon portal ma leh. Marka hore ka samee/ka hawlgeli akoonka bogga macallinka.")
                if start_date < date.today():
                    raise ValueError("Qoondeyn cusub dib looguma qori karo taariikh hore.")
                current = ClubTeamLeaderAssignment.query.filter(
                    ClubTeamLeaderAssignment.team_id == team.id,
                    ClubTeamLeaderAssignment.start_date <= start_date,
                    db.or_(ClubTeamLeaderAssignment.end_date.is_(None), ClubTeamLeaderAssignment.end_date > start_date),
                ).order_by(ClubTeamLeaderAssignment.start_date.desc()).first()
                if current and current.start_date > start_date:
                    raise ValueError("Wareejintu kama hor mari karto wareegga hadda la qorsheeyey.")
                if current and current.start_date == start_date and current.teacher_id == teacher.id and current.club_id == club.id:
                    raise ValueError("Kooxdan hore ayaa loogu qoondeeyey hoggaamiyahan iyo naadiga taariikhdaas.")
                future_assignment = ClubTeamLeaderAssignment.query.filter(
                    ClubTeamLeaderAssignment.team_id == team.id,
                    ClubTeamLeaderAssignment.start_date > start_date,
                    db.or_(ClubTeamLeaderAssignment.end_date.is_(None), ClubTeamLeaderAssignment.end_date > start_date),
                ).first()
                if future_assignment:
                    raise ValueError("Kooxdan waxay leedahay wareejin mustaqbal ah; marka hore dib u eeg taariikhdaas.")
                if current:
                    current.end_date = start_date
                db.session.add(ClubTeamLeaderAssignment(
                    team=team, club=club, teacher=teacher, start_date=start_date,
                    assigned_by_user_id=getattr(current_user, "id", None),
                    transfer_note=request.form.get("transfer_note", "").strip() or None,
                ))
                audit("Club Teams", f"Assigned {team.name} to {teacher.full_name} / {club.name} from {start_date.isoformat()}")
                flash("Hoggaamiyaha/naadiga waa la qoondeeyey; wareegyadii hore way kaydsan yihiin.", "success")
            else:
                raise ValueError("Ficil aan la aqoonsan.")
            db.session.commit()
        except (ValueError, TypeError) as exc:
            db.session.rollback()
            flash(str(exc), "danger")
        return redirect(url_for("admin_teachers.club_teams", team_id=request.form.get("team_id", "")))

    years = AcademicYear.query.order_by(AcademicYear.name.desc()).all()
    selected_year_id = request.args.get("year_id", type=int) or (years[0].id if years else None)
    teams_query = ClubTeam.query.filter_by(academic_year_id=selected_year_id) if selected_year_id else ClubTeam.query.filter(db.false())
    teams = teams_query.order_by(ClubTeam.name).all()
    selected_team_id = request.args.get("team_id", type=int)
    selected_team = next((item for item in teams if item.id == selected_team_id), None) or (teams[0] if teams else None)
    today = date.today()
    active_memberships = {}
    active_assignment = None
    if selected_team:
        active_memberships = {
            item.enrollment_id: item for item in ClubTeamMembership.query.filter(
                ClubTeamMembership.team_id == selected_team.id,
                ClubTeamMembership.start_date <= today,
                db.or_(ClubTeamMembership.end_date.is_(None), ClubTeamMembership.end_date > today),
            ).all()
        }
        active_assignment = ClubTeamLeaderAssignment.query.filter(
            ClubTeamLeaderAssignment.team_id == selected_team.id,
            ClubTeamLeaderAssignment.start_date <= today,
            db.or_(ClubTeamLeaderAssignment.end_date.is_(None), ClubTeamLeaderAssignment.end_date > today),
        ).order_by(ClubTeamLeaderAssignment.start_date.desc()).first()
    enrollments = StudentEnrollment.query.filter_by(
        academic_year_id=selected_team.academic_year_id, status="active",
    ).order_by(StudentEnrollment.academic_year_level_id, StudentEnrollment.academic_year_class_id, StudentEnrollment.id).all() if selected_team else []
    clubs = StudentClub.query.filter_by(is_active=True).order_by(StudentClub.name).all()
    teachers = Teacher.query.filter_by(is_active=True, employment_status="Active").order_by(Teacher.full_name).all()
    history = ClubTeamLeaderAssignment.query.filter_by(team_id=selected_team.id).order_by(ClubTeamLeaderAssignment.start_date.desc()).all() if selected_team else []
    membership_history = ClubTeamMembership.query.filter_by(team_id=selected_team.id).order_by(ClubTeamMembership.start_date.desc(), ClubTeamMembership.id.desc()).all() if selected_team else []
    return render_template(
        "admin/teachers/club_teams.html", years=years, selected_year_id=selected_year_id,
        teams=teams, selected_team=selected_team, active_memberships=active_memberships,
        active_assignment=active_assignment, enrollments=enrollments, clubs=clubs,
        teachers=teachers, history=history, membership_history=membership_history, today=today,
    )
